#!/usr/bin/env python3
"""Backtest the configured strategy + risk settings on past SOL/IDR candles from Indodax.

  python3 bot/backtest.py              # uses config/bot-config.json, writes docs/data/backtest.json
  python3 bot/backtest.py --days 180

The AI is NOT simulated (calling an LLM for every past candle is slow, costly and leaks hindsight);
for ai_supertrend the Supertrend rule (the bot's own fallback) stands in for it. Fees are included.
Results are history, not a promise.
"""
import argparse, json, math, os, sys, time
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config                                     # noqa: E402
import engine as E                                                 # noqa: E402
from strategies import want_series, supertrend, trend_filter, TF_MINUTES, STRATEGY_INFO, WARMUP  # noqa: E402
import aibot as A                                                  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "data", "backtest.json")
WIB = timezone(timedelta(hours=7))


def daily_guards(daily):
    """Per daily candle: realised 30d annual vol and ATR% (known at that day's close)."""
    c = [b["c"] for b in daily]
    atr = supertrend(daily)[3]
    out = {}
    for i in range(len(daily)):
        if i < 31: continue
        r = [math.log(c[j] / c[j - 1]) for j in range(i - 29, i + 1)]
        mu = sum(r) / len(r)
        vol = math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(365)
        out[daily[i]["t"] + 86400] = {"_vol": vol, "_datr_pct": 100 * atr[i] / c[i] if atr[i] else None}
    return out


def simulate(bars, daily, strategy, cfg, start_ts):
    """Returns (book, curve, kills). kills = [{"on": date, "loss_pct", "resume": date|None}] for the total kill switch.
    Total kill: positions closed, no new entries for risk.kill_pause_days, then trading resumes with the
    total-loss baseline reset to the equity at resume (kill_pause_days = 0 -> stopped for good, old behaviour).
    strategy.trend_filter_ma: new entries only while the last closed daily candle is above its SMA(N)."""
    R = cfg["risk"]
    tf_sec = TF_MINUTES[cfg["strategy"]["timeframe"]] * 60
    want = want_series(strategy, bars)
    trend = trend_filter(bars, daily, cfg["strategy"].get("trend_filter_ma", 0), tf_sec)
    guards = daily_guards(daily); gkeys = sorted(guards)
    b = E.new_book(cfg["paper"]["start_idr"])
    curve, kills, stopped, gi, g = [], [], False, 0, {"_vol": 0.6, "_datr_pct": None}
    blocked_trend = 0
    first = next(i for i, x in enumerate(bars) if x["t"] >= start_ts)
    hold_sol = None
    day = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
    for i in range(max(first, 30), len(bars)):
        bar = bars[i]; ts = bar["t"] + tf_sec; px = bar["c"]
        while gi < len(gkeys) and gkeys[gi] <= ts: g = guards[gkeys[gi]]; gi += 1
        if hold_sol is None: hold_sol = cfg["paper"]["start_idr"] * (1 - E.BUY_FEE) / bar["o"]
        E.roll_day(b, datetime.fromtimestamp(ts, WIB).strftime("%Y-%m-%d"), bar["o"])
        if E.maybe_resume(b, ts, bar["o"]) and kills: kills[-1]["resume"] = day(ts)
        if not stopped:
            hit = E.risk_exit(b, R, ts, px, bar["l"], bar["h"])
            if hit:
                E.close_all(b, hit[1], ts, hit[0], R); b["reentry_block"] = True
            lc = E.loss_check(b, R, px, ts)
            if lc:
                E.close_all(b, px, ts, "kill_" + lc[0], R)
                if lc[0] == "daily": b["daily_block"] = b["day"]
                else:
                    kills.append({"on": day(ts), "loss_pct": lc[1], "resume": None})
                    if not E.start_pause(b, R, ts): stopped = True
        if not stopped:
            w = want[i]
            if w == 0: b["reentry_block"] = False
            cur = E.exposure(b, px)
            target = float(w) if (w == 0 or not b["position"]) else cur
            final = min(target, E.caps(R, g))
            if final > 0 and cur < 0.0005 and E.entry_blocked(b, R, ts): final = 0.0
            if final > 0 and cur < 0.0005 and not trend[i]: final = 0.0; blocked_trend += 1
            E.rebalance_to(b, final, px, ts, "signal", R)
        curve.append((ts, E.equity(b, px), hold_sol * px))
    b["_blocked_trend_bars"] = blocked_trend
    return b, curve, kills


def stats(b, curve, start):
    eq = [c[1] for c in curve]; hold = [c[2] for c in curve]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    hpeak, hmdd = hold[0], 0.0
    for v in hold:
        hpeak = max(hpeak, v); hmdd = min(hmdd, v / hpeak - 1)
    tr = b["trades"]; wins = [t for t in tr if t["pnl_idr"] > 0]
    gp = sum(t["pnl_idr"] for t in wins); gl = -sum(t["pnl_idr"] for t in tr if t["pnl_idr"] <= 0)
    reasons = {}
    for t in tr: reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return {"return_pct": round(100 * (eq[-1] / start - 1), 2), "hold_return_pct": round(100 * (hold[-1] / start - 1), 2),
            "max_drawdown_pct": round(100 * mdd, 2), "hold_max_drawdown_pct": round(100 * hmdd, 2),
            "trades": len(tr), "win_rate_pct": round(100 * len(wins) / len(tr), 1) if tr else None,
            "avg_trade_pct": round(sum(t["pnl_pct"] for t in tr) / len(tr), 2) if tr else None,
            "best_trade_pct": max((t["pnl_pct"] for t in tr), default=None),
            "worst_trade_pct": min((t["pnl_pct"] for t in tr), default=None),
            "profit_factor": round(gp / gl, 2) if gl > 0 else None,
            "final_equity": round(eq[-1]), "exit_reasons": reasons, "open_position": bool(b["position"]),
            "trend_blocked_bars": b.get("_blocked_trend_bars", 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int); ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    cfg, warns, _ = load_config()
    days = max(30, min(365, a.days or cfg["backtest"]["days"]))
    tf = cfg["strategy"]["timeframe"]; m = TF_MINUTES[tf]
    ma = cfg["strategy"].get("trend_filter_ma", 0)
    warm_days = max(math.ceil(WARMUP * m / 1440) + 2, ma + 15)     # SMA(ma) needs ma daily closes before day 1
    t0 = time.time()
    bars = A.fetch_bars(tf, days=days + warm_days)
    daily = bars if tf == "1d" else A.fetch_bars("1d", days=days + max(45, ma + 15))
    start_ts = int(time.time()) - days * 86400
    start = cfg["paper"]["start_idr"]
    results, main_res = {}, None
    for strat in ("ai_supertrend", "ema_rsi", "bollinger"):
        b, curve, kills = simulate(bars, daily, strat, cfg, start_ts)
        st = stats(b, curve, start)
        st["killed_on"] = kills[0]["on"] if kills else None; st["kill_events"] = kills
        results[strat] = st
        if strat == cfg["strategy"]["name"]:
            step = max(1, len(curve) // 300)
            pts = curve[::step] + ([curve[-1]] if (len(curve) - 1) % step else [])
            main_res = {"stats": st, "equity": [{"t": c[0], "bot": round(c[1]), "hold": round(c[2])} for c in pts],
                        "trades": b["trades"][-100:]}
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "days": days, "timeframe": tf,
           "bars": len(bars), "strategy": cfg["strategy"]["name"], "strategy_desc": STRATEGY_INFO[cfg["strategy"]["name"]],
           "start_idr": start, "risk": cfg["risk"], "trend_filter_ma": ma, "config_warnings": warns,
           "note": "AI tidak disimulasikan; untuk ai_supertrend dipakai aturan Supertrend (cadangan bot). Fee Indodax dihitung. "
                   + (f"Filter tren: entry baru hanya saat close harian > SMA{ma}. " if ma else "")
                   + (f"Kill switch total = jeda {cfg['risk'].get('kill_pause_days')} hari lalu lanjut. " if cfg["risk"].get("kill_pause_days") else "")
                   + "Hasil masa lalu bukan jaminan.",
           **main_res, "compare": results, "runtime_s": round(time.time() - t0, 1)}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f: json.dump(out, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: out[k] for k in ("days", "timeframe", "bars", "strategy")} | {"stats": out["stats"]}, indent=1))
    for k, v in results.items():
        print(f"{k:14s} return {v['return_pct']:>8}%  mdd {v['max_drawdown_pct']:>7}%  trades {v['trades']:>4}  win {v['win_rate_pct']}  hold {v['hold_return_pct']}%")


if __name__ == "__main__":
    main()
