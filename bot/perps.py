"""Hyperliquid perps venue (PAPER ONLY): multi-agent decisions + deterministic risk agent.

Called from aibot.tick() every scheduled run:
  * every tick: funding accrual, stop / take-profit / liquidation checks on 5m candles since the last
    check, breakeven stop move, daily loss limit + total-loss kill switch
  * on a new closed candle of perps.timeframe: 6 analyst agents (one batched LLM call for all pairs,
    rule fallback) -> coordinator -> risk agent veto/plan -> paper fills
State lives in state["perps"]; every vote and veto is kept in state for the app ("Agen" tab).
"""
import time
from datetime import datetime, timezone, timedelta

import agents as AG
import hyperliquid as HL

WIB = timezone(timedelta(hours=7))
MAX_DECISIONS, MAX_EQUITY = 240, 2000
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def usd(v): return ("-" if v < 0 else "") + f"${abs(v):,.2f}"


def fp(v):
    """Price for messages: 81,750 · 103.52 · 0.1234"""
    if v is None: return "—"
    a = abs(v)
    return f"{v:,.0f}" if a >= 1000 else f"{v:,.2f}" if a >= 10 else f"{v:.4f}" if a >= 0.1 else f"{v:.6g}"


def new_state(start):
    return {"version": 1, "mode": "PAPER", "book": HL.new_book(start), "last_candle": None, "latest": {},
            "decisions": [], "equity": [], "hold": {"px0": {}, "start": float(start)}, "oi_hist": {},
            "last_check": {}, "reset_done": "", "fng": None, "llm": None}


def ensure(s, P):
    ps = s.get("perps")
    if not isinstance(ps, dict) or ps.get("version") != 1:
        ps = s["perps"] = new_state(P["paper_start_usd"])
    for k, v in new_state(P["paper_start_usd"]).items(): ps.setdefault(k, v)
    return ps


def fear_greed(get):
    try:
        d = get("https://api.alternative.me/fng/?limit=2", timeout=15)["data"]
        return {"value": int(d[0]["value"]), "prev": int(d[1]["value"]) if len(d) > 1 else None,
                "label": d[0].get("value_classification")}
    except Exception:
        return None


def book_view(ps, prices):
    b = ps["book"]
    eq = HL.equity(b, prices)
    dl, tl = HL.loss_state(b, prices)
    return {"equity": eq, "free_margin": HL.free_margin(b, prices), "open_positions": len(b["positions"]),
            "daily_loss_pct": dl, "total_loss_pct": tl, "kill_active": bool(b.get("kill")), "daily_block": b.get("daily_block")}


def decide_pairs(ps, P, feats, votes, prices, max_levs, ts, on_trade):
    """Coordinator + risk agent for every pair; opens/closes paper positions. Shared with the backtest.
    on_trade(kind, coin, obj) is called for every open/close. Returns {coin: decision}."""
    b = ps["book"]
    out = {}
    for coin in feats:
        f, v = feats[coin], votes[coin]
        coord = AG.coordinate(v, P["entry_threshold"], P["allow_short"])
        pos = b["positions"].get(coin)
        closed = None
        if pos and coord["direction"] in ("long", "short") and coord["direction"] != pos["side"]:
            closed = HL.close_position(b, coin, prices[coin], ts, "sinyal berbalik")
            on_trade("close", coin, closed)
        bs = book_view(ps, prices); bs["has_position"] = coin in b["positions"]
        risk = AG.risk_agent(coin, coord, f, P, bs, max_levs.get(coin, 5))
        opened = None
        if risk["approved"]:
            try:
                opened = HL.open_position(b, coin, risk["plan"], prices[coin], ts, f"multi-agen {coord['score']:+.2f}")
                on_trade("open", coin, opened)
            except ValueError as e:
                risk = dict(risk, approved=False, veto=f"Engine menolak: {e}", plan=risk["plan"])
        action = ("OPEN " + opened["side"].upper()) if opened else ("CLOSE" if closed else ("HOLD" if coin in b["positions"] else "—"))
        out[coin] = {"coin": coin, "ts": ts, "price": prices[coin], "votes": v, "coordinator": coord,
                     "risk": {"approved": risk["approved"], "veto": risk["veto"], "plan": risk["plan"], "checks": risk["checks"]},
                     "action": action, "features": {k: f.get(k) for k in ("rsi14", "atr_pct", "efficiency_ratio", "supertrend",
                                                                           "funding_apr_pct", "oi_change_24h_pct", "fear_greed",
                                                                           "book_imbalance", "spread_bps", "roc20_pct")}}
    return out


def tick(cfg, s, ts, force, ask_json, event, iso, get, close_request=False, enabled=True):
    P = cfg["perps"]
    ps = ensure(s, P)
    b = ps["book"]
    pairs = list(P["pairs"])

    # reset / start balance change
    if P.get("reset_request") and P["reset_request"] != ps.get("reset_done"):
        ps.update(new_state(P["paper_start_usd"]), reset_done=P["reset_request"]); b = ps["book"]
        event(s, "info", "Paper perps direset", f"Saldo simulasi Hyperliquid: {usd(P['paper_start_usd'])}.", "PAPER")
    elif not b["positions"] and not b["trades"] and b["start_usd"] != P["paper_start_usd"]:
        ps.update(new_state(P["paper_start_usd"]), reset_done=ps.get("reset_done", "")); b = ps["book"]

    ctx = HL.market_ctx()
    unknown = [c for c in pairs if c not in ctx]
    if unknown:
        event(s, "error", "Pair Hyperliquid tidak dikenal", ", ".join(unknown), "PAPER", dedupe_hours=24)
        pairs = [c for c in pairs if c in ctx]
    watch = sorted(set(pairs) | set(b["positions"]))
    prices = {c: ctx[c]["mid"] for c in watch if c in ctx}
    ps["prices"] = {c: prices[c] for c in watch if c in prices}
    ps["prices_at"] = iso(ts)

    # open-interest history (own samples, ~hourly) for the funding/OI agent
    for c in pairs:
        h = ps["oi_hist"].setdefault(c, [])
        if not h or ts - h[-1][0] >= 3000: h.append([ts, ctx[c]["oi"]])
        del h[:-40]

    # hourly funding on open positions
    hour = ts // 3600
    last_h = b.get("last_funding_hour")
    if last_h is None: b["last_funding_hour"] = hour
    elif hour > last_h:
        hrs = min(hour - last_h, 24)
        for c in list(b["positions"]):
            if c in ctx: HL.apply_funding(b, c, ctx[c]["funding"], prices[c], hrs)
        b["last_funding_hour"] = hour

    def trade_ev(kind, coin, o):
        if not o: return
        if kind == "open":
            event(s, "trade", f"OPEN {o['side'].upper()} {coin} · PERP PAPER",
                  f"{o['size']:.4g} {coin} @ {fp(o['entry'])} ({usd(o['notional'])}, {o['leverage']}x). "
                  f"Stop {fp(o['stop'])} · TP {fp(o['tp'])} · Likuidasi {fp(o['liq'])}. Risiko {usd(o['risk_usd'])}.", "PAPER")
        else:
            typ = "risk" if o["exit_reason"] in ("stoploss", "take_profit", "liquidation", "breakeven_stop") or o["exit_reason"].startswith("kill") else "trade"
            lbl = {"stoploss": "Stop-loss", "take_profit": "Take-profit", "liquidation": "LIKUIDASI", "breakeven_stop": "Stop impas"}.get(o["exit_reason"], "Tutup")
            event(s, typ, f"{lbl} {o['side'].upper()} {coin} · PERP PAPER",
                  f"Keluar @ {fp(o['exit'])} (masuk {fp(o['entry'])}). PnL {usd(o['pnl_usd'])} ({o['r_multiple']}R), "
                  f"fee {usd(o['fees_usd'])}, funding {usd(-o['funding_usd'])}. Alasan: {o['exit_reason']}.", "PAPER")

    # stop / TP / liquidation on 5m candles since the last check (then the live mid)
    for c, p in list(b["positions"].items()):
        since = max(ps["last_check"].get(c, p["opened_at"]), ts - 86400)
        try: bars = HL.candles(c, "5m", since * 1000, ts * 1000)
        except Exception: bars = []
        hit = None
        for bar in bars:
            if bar["t"] + 300 <= since: continue
            hit = HL.check_exit(p, bar["l"], bar["h"], bar["o"] if bar["t"] >= since else None)
            if hit: break
            HL.maybe_breakeven(p, bar["l"], bar["h"], P["breakeven_at_r"])
        if not hit and c in prices: hit = HL.check_exit(p, prices[c], prices[c])
        if hit:
            t = HL.close_position(b, c, hit[1], ts, hit[0], liquidated=hit[2])
            trade_ev("close", c, t)
    for c in list(b["positions"]) + pairs: ps["last_check"][c] = ts

    # manual close (app) applies to perps too
    if close_request and b["positions"]:
        for c in list(b["positions"]): trade_ev("close", c, HL.close_position(b, c, prices.get(c, b["positions"][c]["entry"]), ts, "tutup manual dari aplikasi"))

    # daily window + loss limits / kill switch
    day = datetime.fromtimestamp(ts, WIB).strftime("%Y-%m-%d")
    HL.roll_day(b, day, prices)
    dl, tl = HL.loss_state(b, prices)
    if P["kill_switch_enabled"] and tl >= P["total_max_loss_pct"] and not b.get("kill"):
        for c in list(b["positions"]): trade_ev("close", c, HL.close_position(b, c, prices[c], ts, "kill_total"))
        b["kill"] = {"at": iso(ts), "loss_pct": tl}
        event(s, "kill", "KILL SWITCH perps", f"Rugi total {tl:.2f}% ≥ {P['total_max_loss_pct']}%. Semua posisi paper ditutup; "
              "tidak ada entry baru sampai paper perps direset dari aplikasi.", "PAPER")
    elif P["daily_loss_enabled"] and dl >= P["daily_max_loss_pct"] and not b.get("daily_block"):
        for c in list(b["positions"]): trade_ev("close", c, HL.close_position(b, c, prices[c], ts, "kill_daily"))
        b["daily_block"] = day
        event(s, "kill", "Batas rugi harian perps", f"Rugi hari ini {dl:.2f}% ≥ {P['daily_max_loss_pct']}%. Tidak ada entry baru sampai besok (WIB).", "PAPER")

    # evaluation on a newly closed candle
    tf = P["timeframe"]; step = TF_SEC[tf]
    last_closed = (ts // step) * step - step
    key = datetime.fromtimestamp(last_closed, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") + "|" + ",".join(pairs)
    if enabled and pairs and (force or ps.get("last_candle") != key):
        evaluate(cfg, s, ps, pairs, ctx, prices, ts, ask_json, event, get, trade_ev)
        ps["last_candle"] = key

    # equity + equal-weight hold benchmark
    for c in pairs: ps["hold"]["px0"].setdefault(c, prices[c])
    eq = HL.equity(b, prices)
    hp = [prices[c] / ps["hold"]["px0"][c] for c in pairs if c in ps["hold"]["px0"] and c in prices]
    hold_eq = ps["hold"]["start"] * (sum(hp) / len(hp)) if hp else None
    last = ps["equity"][-1] if ps["equity"] else None
    if not last or ts - last["t"] >= 3600 or b["positions"] != {} and ts - last["t"] >= 900:
        ps["equity"].append({"t": ts, "eq": round(eq, 2), "hold": round(hold_eq, 2) if hold_eq else None})
        del ps["equity"][:-MAX_EQUITY]
    ps["summary"] = summary(ps, prices, hold_eq)
    s["perps"] = ps


def evaluate(cfg, s, ps, pairs, ctx, prices, ts, ask_json, event, get, trade_ev):
    P = cfg["perps"]
    fng = fear_greed(get)
    ps["fng"] = fng
    feats = {}
    for c in pairs:
        bars = HL.closed_candles(c, P["timeframe"], 320)
        if len(bars) < 60: continue
        try: bk = HL.book_stats(*HL.l2_book(c))
        except Exception: bk = None
        h = ps["oi_hist"].get(c) or []
        oi_chg = None
        old = [x for x in h if ts - x[0] >= 20 * 3600]
        if old and old[-1][1]: oi_chg = 100 * (ctx[c]["oi"] / old[-1][1] - 1)
        feats[c] = AG.features(bars, ctx[c], fng, bk, oi_chg)
        feats[c]["close"] = prices[c]          # size/stop against the live mid, not the last close
    if not feats: return
    use_llm = bool(cfg["ai"]["enabled"] and P["use_llm"] and ask_json)
    votes, src, err = AG.analyst_votes(feats, ask_json if use_llm else None)
    if not use_llm:
        src = "Aturan (AI agen dimatikan)" if not (cfg["ai"]["enabled"] and P["use_llm"]) else "Aturan (tanpa key AI)"
    ps["llm"] = {"source": src, "error": err, "ts": ts}
    max_levs = {c: ctx[c]["max_leverage"] for c in feats}
    dec = decide_pairs(ps, P, feats, votes, prices, max_levs, ts, trade_ev)
    for c, d in dec.items():
        d["source"] = src
        ps["latest"][c] = d
        ps["decisions"].append({"ts": ts, "coin": c, "dir": d["coordinator"]["direction"], "score": d["coordinator"]["score"],
                                "approved": d["risk"]["approved"], "veto": d["risk"]["veto"], "action": d["action"],
                                "price": d["price"], "votes": {a: [v["dir"], v["conf"]] for a, v in d["votes"].items()}, "source": src})
    del ps["decisions"][:-MAX_DECISIONS]
    for c in list(ps["latest"]):
        if c not in pairs: ps["latest"].pop(c, None)
    parts = [f"{c} {d['coordinator']['direction']} {d['coordinator']['score']:+.2f} → "
             + (d["action"] if d["action"] != "—" else "veto: " + str(d["risk"]["veto"])) for c, d in dec.items()]
    event(s, "agent", f"Agen · {P['timeframe']} · {src}", "; ".join(parts)[:600], "PAPER")
    if err and use_llm:
        event(s, "fallback" if src.startswith("Cadangan") else "info", "Agen AI → aturan" if src.startswith("Cadangan") else "Agen AI sebagian",
              err[:300], "PAPER", dedupe_hours=12)


def summary(ps, prices, hold_eq):
    b = ps["book"]
    eq = HL.equity(b, prices)
    per = {}
    for t in b["trades"]:
        r = per.setdefault(t["coin"], {"realized": 0.0, "trades": 0, "wins": 0, "unrealized": 0.0})
        r["realized"] += t["pnl_usd"]; r["trades"] += 1; r["wins"] += t["pnl_usd"] > 0
    for c, p in b["positions"].items():
        r = per.setdefault(c, {"realized": 0.0, "trades": 0, "wins": 0, "unrealized": 0.0})
        r["unrealized"] += HL.unrealized(p, prices.get(c, p["entry"])) - p["funding"]
    for r in per.values():
        r["realized"] = round(r["realized"], 2); r["unrealized"] = round(r["unrealized"], 2)
        r["win_rate"] = round(100 * r["wins"] / r["trades"], 1) if r["trades"] else None
    dl, tl = HL.loss_state(b, prices)
    return {"equity": round(eq, 2), "start": b["start_usd"], "pnl_usd": round(eq - b["start_usd"], 2),
            "pnl_pct": round(100 * (eq / b["start_usd"] - 1), 2), "hold_equity": round(hold_eq, 2) if hold_eq else None,
            "balance": round(b["balance"], 2), "used_margin": round(HL.used_margin(b), 2), "fees": round(b["fees_paid"], 2),
            "funding": round(b["funding_paid"], 2), "trades": len(b["trades"]), "open": len(b["positions"]),
            "daily_loss_pct": dl, "total_loss_pct": tl, "kill": b.get("kill"), "daily_block": b.get("daily_block"), "per_pair": per}
