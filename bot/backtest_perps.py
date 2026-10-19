#!/usr/bin/env python3
"""Backtest the multi-agent layer (RULE mode, no LLM) on Hyperliquid perps candles, PAPER only.

  python3 bot/backtest_perps.py               # pairs/timeframe/risk from config, writes docs/data/backtest_perps.json
  python3 bot/backtest_perps.py --days 180

Same code as the live bot: 6 rule analysts -> coordinator -> deterministic risk agent -> paper perps
engine (taker fee, slippage, hourly funding from fundingHistory, leverage, liquidation, stop + TP before
entry, breakeven move, daily loss limit, kill switch). Fear & Greed history from alternative.me.
Not simulated (no history available): order book and open-interest change -> those agent inputs are empty.
Exits use the timeframe candle high/low (stop assumed first if both hit). Past results are no promise.
"""
import argparse, bisect, json, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config                                     # noqa: E402
import agents as AG                                                # noqa: E402
import hyperliquid as HL                                           # noqa: E402
import perps as PERPS                                              # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "data", "backtest_perps.json")
WIB = timezone(timedelta(hours=7))
WARM = 320


def fng_history():
    try:
        req = urllib.request.Request("https://api.alternative.me/fng/?limit=0&format=json", headers={"User-Agent": "solbot/3.0"})
        d = json.load(urllib.request.urlopen(req, timeout=30))["data"]
        return sorted((int(x["timestamp"]), int(x["value"])) for x in d)
    except Exception:
        return []


def load_data(pairs, tf, days):
    step = PERPS.TF_SEC[tf]
    now = int(time.time())
    start_ms = (now - days * 86400 - WARM * step) * 1000
    data, meta = {}, HL.market_ctx()
    for c in pairs:
        if c not in meta: continue
        bars = [b for b in HL.candles(c, tf, start_ms) if b["t"] + step <= now]
        try: fund = HL.funding_history(c, (now - days * 86400 - 2 * 86400) * 1000)
        except Exception as e:
            print(f"funding {c} gagal ({e}); funding diabaikan untuk pair ini", file=sys.stderr); fund = []
        data[c] = {"bars": bars, "funding": fund, "max_lev": meta[c]["max_leverage"]}
        time.sleep(2)
    return data


def simulate(data, P, days, fng):
    step = PERPS.TF_SEC[P["timeframe"]]
    coins = list(data)
    times = sorted(set.intersection(*[set(b["t"] for b in data[c]["bars"]) for c in coins]))
    idx = {c: {b["t"]: i for i, b in enumerate(data[c]["bars"])} for c in coins}
    start_ts = int(time.time()) - days * 86400
    ftimes = {c: [t for t, _ in data[c]["funding"]] for c in coins}
    fng_t = [t for t, _ in fng]
    ps = {"book": HL.new_book(P["paper_start_usd"])}
    b = ps["book"]
    curve, decisions, vetoes, hold0, killed = [], {"long": 0, "short": 0, "flat": 0}, {}, None, None
    for t in times:
        close_ts = t + step
        if close_ts < start_ts: continue
        bar = {c: data[c]["bars"][idx[c][t]] for c in coins}
        prices = {c: bar[c]["c"] for c in coins}
        if hold0 is None: hold0 = {c: bar[c]["o"] for c in coins}
        # exits during this candle
        for c, p in list(b["positions"].items()):
            hit = HL.check_exit(p, bar[c]["l"], bar[c]["h"], bar[c]["o"])
            if hit: HL.close_position(b, c, hit[1], close_ts, hit[0], liquidated=hit[2]); continue
            HL.maybe_breakeven(p, bar[c]["l"], bar[c]["h"], P["breakeven_at_r"])
        # funding for every hour of the candle
        for c, p in list(b["positions"].items()):
            lo = bisect.bisect_left(ftimes[c], t); hi = bisect.bisect_left(ftimes[c], close_ts)
            for _, rate in data[c]["funding"][lo:hi]: HL.apply_funding(b, c, rate, prices[c], 1.0)
        # loss limits
        HL.roll_day(b, datetime.fromtimestamp(close_ts, WIB).strftime("%Y-%m-%d"), prices)
        dl, tl = HL.loss_state(b, prices)
        if not b.get("kill") and P["kill_switch_enabled"] and tl >= P["total_max_loss_pct"]:
            for c in list(b["positions"]): HL.close_position(b, c, prices[c], close_ts, "kill_total")
            b["kill"] = {"at": close_ts, "loss_pct": tl}
            killed = datetime.fromtimestamp(close_ts, timezone.utc).strftime("%Y-%m-%d")
        elif P["daily_loss_enabled"] and dl >= P["daily_max_loss_pct"] and not b.get("daily_block"):
            for c in list(b["positions"]): HL.close_position(b, c, prices[c], close_ts, "kill_daily")
            b["daily_block"] = b["day"]
        # agents on the closed candle
        feats, votes = {}, {}
        k = bisect.bisect_right(fng_t, close_ts) - 1
        fg = {"value": fng[k][1], "prev": fng[k - 1][1] if k > 0 else None} if k >= 0 else None
        for c in coins:
            i = idx[c][t]
            if i < 210: continue
            fi = bisect.bisect_right(ftimes[c], close_ts) - 1
            ctx = {"funding": data[c]["funding"][fi][1]} if fi >= 0 else {}
            feats[c] = AG.features(data[c]["bars"][max(0, i - WARM + 1):i + 1], ctx, fg, None, None)
            votes[c] = AG.rule_votes(feats[c])
        if feats:
            dec = PERPS.decide_pairs(ps, P, feats, votes, prices, {c: data[c]["max_lev"] for c in coins}, close_ts, lambda *a: None)
            for d in dec.values():
                decisions[d["coordinator"]["direction"]] += 1
                v = d["risk"]["veto"]
                if v and d["coordinator"]["direction"] != "flat":
                    key = v.split(" (")[0][:60]
                    vetoes[key] = vetoes.get(key, 0) + 1
        hold = P["paper_start_usd"] * (1 - HL.TAKER_FEE) * sum(prices[c] / hold0[c] for c in coins) / len(coins)
        curve.append((close_ts, HL.equity(b, prices), hold))
    return b, curve, decisions, vetoes, killed, hold0, prices


def dd(series):
    peak, m = series[0], 0.0
    for v in series:
        peak = max(peak, v); m = min(m, v / peak - 1)
    return round(100 * m, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int); ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    cfg, warns, _ = load_config()
    P = cfg["perps"]
    days = max(30, min(365, a.days or cfg["backtest"]["days"]))
    t0 = time.time()
    cache = os.environ.get("BT_CACHE")          # optional local cache for repeated runs
    if cache and os.path.exists(cache): data = json.load(open(cache))
    else:
        data = load_data(P["pairs"], P["timeframe"], days)
        if cache: json.dump(data, open(cache, "w"))
    if not data: sys.exit("tidak ada data Hyperliquid untuk pair yang dipilih")
    fng = fng_history()
    b, curve, decisions, vetoes, killed, hold0, last_px = simulate(data, P, days, fng)
    start = P["paper_start_usd"]
    eq = [c[1] for c in curve]; hold = [c[2] for c in curve]
    tr = b["trades"]; wins = [t for t in tr if t["pnl_usd"] > 0]
    gp = sum(t["pnl_usd"] for t in wins); gl = -sum(t["pnl_usd"] for t in tr if t["pnl_usd"] <= 0)
    reasons, per = {}, {}
    for t in tr:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
        r = per.setdefault(t["coin"], {"trades": 0, "wins": 0, "pnl_usd": 0.0, "long": 0, "short": 0})
        r["trades"] += 1; r["wins"] += t["pnl_usd"] > 0; r["pnl_usd"] += t["pnl_usd"]; r[t["side"]] += 1
    for c, r in per.items():
        r["pnl_usd"] = round(r["pnl_usd"], 2); r["win_rate_pct"] = round(100 * r["wins"] / r["trades"], 1) if r["trades"] else None
    for c in data:
        per.setdefault(c, {"trades": 0, "wins": 0, "pnl_usd": 0.0, "long": 0, "short": 0, "win_rate_pct": None})
        per[c]["hold_return_pct"] = round(100 * (last_px[c] / hold0[c] - 1), 2)
    st = {"return_pct": round(100 * (eq[-1] / start - 1), 2), "hold_return_pct": round(100 * (hold[-1] / start - 1), 2),
          "max_drawdown_pct": dd(eq), "hold_max_drawdown_pct": dd(hold), "trades": len(tr),
          "win_rate_pct": round(100 * len(wins) / len(tr), 1) if tr else None,
          "avg_r": round(sum(t["r_multiple"] or 0 for t in tr) / len(tr), 2) if tr else None,
          "profit_factor": round(gp / gl, 2) if gl > 0 else None, "final_equity": round(eq[-1], 2),
          "fees_usd": round(b["fees_paid"], 2), "funding_usd": round(b["funding_paid"], 2),
          "exit_reasons": reasons, "killed_on": killed, "open_positions": len(b["positions"])}
    step = max(1, len(curve) // 300)
    pts = curve[::step] + ([curve[-1]] if (len(curve) - 1) % step else [])
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "venue": "hyperliquid", "mode": "rule (tanpa LLM)",
           "days": days, "timeframe": P["timeframe"], "pairs": list(data), "start_usd": start, "stats": st, "per_pair": per,
           "coordinator": decisions, "risk_vetoes": dict(sorted(vetoes.items(), key=lambda kv: -kv[1])),
           "settings": {k: P[k] for k in ("max_leverage", "risk_per_trade_pct", "max_positions", "stop_atr_mult", "take_profit_r",
                                           "breakeven_at_r", "liq_buffer_mult", "entry_threshold", "allow_short",
                                           "daily_max_loss_pct", "total_max_loss_pct")},
           "equity": [{"t": c[0], "bot": round(c[1], 2), "hold": round(c[2], 2)} for c in pts],
           "trades": tr[-100:], "config_warnings": warns, "fear_greed_points": len(fng),
           "funding_points": {c: len(d["funding"]) for c, d in data.items()},
           "note": "Mode aturan (tanpa LLM). Fee taker 0,045% + slippage 0,02%, funding per jam dari Hyperliquid. Orderbook & perubahan OI tidak punya data historis → agen likuiditas pakai volume saja, agen funding pakai funding saja. Hold = rata-rata beli & tahan semua pair (1x). Hasil masa lalu bukan jaminan.",
           "runtime_s": round(time.time() - t0, 1)}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f: json.dump(out, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: out[k] for k in ("days", "timeframe", "pairs", "stats", "per_pair", "coordinator", "risk_vetoes", "runtime_s")}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
