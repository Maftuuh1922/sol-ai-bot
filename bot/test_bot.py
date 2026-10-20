"""Offline tests: python3 bot/test_bot.py  (no network, no AI, never live)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
os.environ["STATE_FILE"] = os.path.join(tmp, "state.json")
os.environ["BOT_CONFIG"] = os.path.join(tmp, "cfg.json")
for k in ("NAFNAF_API_KEY", "GONKA_API_KEY", "ARCEE_API_KEY", "INDODAX_API_KEY", "INDODAX_KEY", "INDODAX_SECRET", "BOT_MODE"):
    os.environ.pop(k, None)
import engine as E, config as C, aibot as A  # noqa: E402

R = C.DEFAULTS["risk"]
ok = 0
def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1

# --- engine ---
b = E.new_book(1_000_000)
o = E.buy(b, 1_000_000, 100.0, 0, "t")
check(b["position"] and abs(b["position"]["entry"] - 100 / (1 - E.BUY_FEE)) < 1e-6, "entry incl fee")
check(E.risk_exit(b, R, 60, 95.0) is None, "no stop at -5%")
check(E.risk_exit(b, R, 60, 89.0)[0] == "stoploss", "stoploss at -10%")
r2 = dict(R, stoploss_enabled=False, trailing_enabled=True, trailing_pct=5, trailing_activate_pct=3)
check(E.risk_exit(b, r2, 60, 110.0) is None, "trailing activates")
check(E.risk_exit(b, r2, 60, 104.0)[0] == "trailing_stop", "trailing hit (110 -> 104)")
r3 = dict(R, stoploss_enabled=False, roi_enabled=True, minimal_roi={"0": 20, "60": 5})
check(E.risk_exit(b, r3, 30, 110.0) is None, "roi 20% not reached at 30 min")
check(E.risk_exit(b, r3, 3700, 110.0)[0] == "roi", "roi 5% reached after 60 min")
o, closed = E.close_all(b, 80.0, 100, "stoploss", R)
check(closed and closed["pnl_idr"] < 0 and b["position"] is None, "trade closed with loss")
check(b["cooldown_until"] == 100 + 60 * R["cooldown_minutes"], "cooldown after loss")
check(E.entry_blocked(b, R, 200) == "cooldown setelah rugi", "entry blocked in cooldown")
b2 = E.new_book(1_000_000); E.roll_day(b2, "d1", 100.0); E.buy(b2, 1_000_000, 100.0, 0, "t")
check(E.loss_check(b2, R, 94.0) == ("daily", round(100 * (1 - E.equity(b2, 94.0) / 1_000_000), 2)), "daily loss")
check(E.loss_check(b2, R, 70.0)[0] == "total", "total loss")
check(E.rebalance_to(b2, 0.95, 100.0, 1, "x") == (None, None), "min step hold")
o, _ = E.rebalance_to(b2, 0.5, 100.0, 1, "x", R, max_order_idr=100_000)
check(o and o["side"] == "SELL" and abs(o["sol"] * 100 - 100_000) < 1, "max order caps sell")

# --- config validation ---
cfg, w = C.validate({"mode": "LIVE", "risk": {"max_exposure_pct": 400, "stoploss_pct": "abc"},
                     "ai": {"extra_instruction": "x" * 900, "providers": [{"name": "gonka"}, {"name": "evil"}]}})
check(cfg["mode"] == "live" and cfg["risk"]["max_exposure_pct"] == 100 and cfg["risk"]["stoploss_pct"] == 10, "clamp")
check(len(cfg["ai"]["extra_instruction"]) == 500 and cfg["ai"]["providers"][0]["name"] == "gonka", "ai fields")
open(os.environ["BOT_CONFIG"], "w").write('{"mode": "live", oops')
cfg, w, fok = C.load_config(env={"BOT_MODE": "live"})
check(not fok and cfg["mode"] == "paper" and w, "corrupt config -> paper")

# --- tick with a fake market (never live) ---
price = {"v": 100.0}
A.ticker = lambda: price["v"]
bars = [dict(t=1_700_000_000 + i * 86400, o=100.0, h=101.0, l=99.0, c=100.0 + i * 0.5) for i in range(320)]
A.fetch_bars = lambda tf, n=None, days=None, closed_only=True: bars
def write_cfg(**kw):
    c = json.loads(json.dumps(C.DEFAULTS)); c["venue"] = "indodax"; c.update(kw); json.dump(c, open(os.environ["BOT_CONFIG"], "w"))
write_cfg(mode="live")
A.tick(force=True)
s = json.load(open(os.environ["STATE_FILE"]))
check(s["mode"] == "PAPER" and any(e["title"].startswith("Live ditolak") for e in s["events"]), "live w/o keys -> paper")
check(s["books"]["paper"]["position"], "supertrend fallback bought in uptrend")
write_cfg(enabled=False)
A.tick()
s = json.load(open(os.environ["STATE_FILE"]))
check(s["status"] == "disabled" and s["books"]["paper"]["position"], "disabled keeps position")
write_cfg(enabled=False, close_position_request="req-1")
A.tick()
s = json.load(open(os.environ["STATE_FILE"]))
check(s["books"]["paper"]["position"] is None and s["books"]["paper"]["trades"], "manual close works while off")
write_cfg()
A.tick(force=True)
s = json.load(open(os.environ["STATE_FILE"]))
check(s["books"]["paper"]["position"], "re-entered")
price["v"] = 60.0
A.tick()
s = json.load(open(os.environ["STATE_FILE"]))
cfgnow = json.load(open(os.environ["BOT_CONFIG"]))
check(any(e["type"] == "risk" for e in s["events"]), "stoploss event")
check(len({e["id"] for e in s["events"]}) == len(s["events"]), "event ids unique")
print("events:", [e["title"] for e in s["events"]])
print("config after:", cfgnow["enabled"], cfgnow["mode"])

# --- total kill switch: pause N days, then resume with a new baseline ---
import strategies as S, backtest as BT  # noqa: E402
RP = dict(R, kill_pause_days=14, daily_loss_enabled=False)
b3 = E.new_book(1_000_000); E.buy(b3, 1_000_000, 100.0, 0, "t")
check(E.loss_check(b3, RP, 70.0, 10)[0] == "total", "total kill fires")
E.close_all(b3, 70.0, 10, "kill_total", RP)
until = E.start_pause(b3, RP, 10)
check(until == 10 + 14 * 86400 and E.paused(b3, 11), "pause 14 days")
check(E.entry_blocked(b3, RP, 20) == "jeda kill switch rugi total", "no entries while paused")
check(E.loss_check(b3, RP, 70.0, 20) is None, "total check skipped while paused (no re-trigger)")
check(not E.maybe_resume(b3, until - 1, 70.0) and E.paused(b3, until - 1), "still paused before 14 days")
eq_resume = E.equity(b3, 70.0)
check(E.maybe_resume(b3, until, 70.0) and not E.paused(b3, until), "resumes after 14 days")
check(abs(b3["loss_base"] - eq_resume) < 1e-6 and E.loss_check(b3, RP, 70.0, until) is None, "baseline reset at resume")
check(E.entry_blocked(b3, dict(RP, cooldown_enabled=False), until + 1) is None, "entries allowed after resume")
check(E.start_pause(E.new_book(1), dict(R, kill_pause_days=0), 0) == 0, "kill_pause_days=0 -> no pause (old stop)")
check(C.validate({"risk": {"kill_pause_days": 999}, "strategy": {"trend_filter_ma": -5}})[0]["risk"]["kill_pause_days"] == 365, "pause clamp")
check(C.validate({"strategy": {"trend_filter_ma": -5}})[0]["strategy"]["trend_filter_ma"] == 0, "ma clamp")
check(C.DEFAULTS["risk"]["kill_pause_days"] == 14 and C.DEFAULTS["strategy"]["trend_filter_ma"] == 200, "defaults")

# --- MA200 trend filter: no lookahead, exits unaffected ---
T0 = 1_700_000_000
dd = [dict(t=T0 + i * 86400, o=1.0, h=1.0, l=1.0, c=float(c)) for i, c in enumerate([100] * 200 + [50] * 5 + [500] * 5)]
tf_ok = S.trend_filter(dd, dd, 200, 86400)
check(not any(tf_ok[:199]), "no SMA yet -> no entries")
check(tf_ok[199] is False, "close == SMA -> not above")
check(not any(tf_ok[200:205]), "below SMA blocks")
check(all(tf_ok[205:]), "above SMA allows")
h4 = [dict(t=T0 + i * 14400, o=1, h=1, l=1, c=1) for i in range(205 * 6, 207 * 6)]
f4 = S.trend_filter(h4, dd, 200, 14400)
# day 205 (first 500 close) closes at T0+206*86400; 4h bars closing before that still see day 204 (50 < SMA)
first_true = next(i for i, x in enumerate(f4) if x)
check(h4[first_true]["t"] + 14400 >= T0 + 206 * 86400, "4h bars only use daily candles already closed (no lookahead)")
check(S.trend_filter(dd, dd, 0, 86400) == [True] * len(dd), "ma=0 disables")
check(S.trend_state(dd, 200)[0] and not S.trend_state(dd[:205], 200)[0], "trend_state live")

# --- backtest simulate: crash -> kill -> pause -> resume; MA filter blocks entries in a downtrend ---
def mk(closes):
    return [dict(t=T0 + i * 86400, o=c, h=c * 1.01, l=c * 0.99, c=c) for i, c in enumerate(closes)]
up = [100 * (1.004 ** i) for i in range(260)]
crash = [up[-1] * 0.7] + [up[-1] * 0.7 * (0.99 ** k) for k in range(1, 10)]
rec = [crash[-1] * (1.01 ** k) for k in range(1, 120)]
bars_bt = mk(up + crash + rec)
cfg_bt = json.loads(json.dumps(C.DEFAULTS)); cfg_bt["strategy"]["trend_filter_ma"] = 0
cfg_bt["risk"].update(stoploss_enabled=False, daily_loss_enabled=False, total_max_loss_pct=15, kill_pause_days=14, cooldown_enabled=False)
bk, cv, kills = BT.simulate(bars_bt, bars_bt, "ai_supertrend", cfg_bt, bars_bt[230]["t"])
check(kills and kills[0]["resume"], f"backtest kill resumes {kills}")
from datetime import datetime as _dt
gap = (_dt.strptime(kills[0]["resume"], "%Y-%m-%d") - _dt.strptime(kills[0]["on"], "%Y-%m-%d")).days
check(gap == 14, f"resume after 14 days ({gap})")
check(any(o["side"] == "BUY" and o["ts"] > T0 + 0 for o in bk["orders"] if _dt.utcfromtimestamp(o["ts"]).strftime("%Y-%m-%d") >= kills[0]["resume"]), "trades again after resume")
cfg_bt["risk"]["kill_pause_days"] = 0
bk0, _, kills0 = BT.simulate(bars_bt, bars_bt, "ai_supertrend", cfg_bt, bars_bt[230]["t"])
check(kills0 and kills0[0]["resume"] is None and len(bk0["orders"]) < len(bk["orders"]), "pause 0 = permanent stop")
down = mk([300 * (0.995 ** i) for i in range(260)] + [300 * (0.995 ** 259) * (1.003 ** k) for k in range(1, 60)])
cfg_bt["risk"]["kill_pause_days"] = 14
for strat in ("ai_supertrend", "ema_rsi", "bollinger"):
    cfg_bt["strategy"]["trend_filter_ma"] = 200
    bf, _, _ = BT.simulate(down, down, strat, cfg_bt, down[210]["t"])
    check(not any(o["side"] == "BUY" for o in bf["orders"]), f"{strat}: MA200 blocks new longs below SMA200")
cfg_bt["strategy"]["trend_filter_ma"] = 0
bn, _, _ = BT.simulate(down, down, "ai_supertrend", cfg_bt, down[210]["t"])
check(any(o["side"] == "BUY" for o in bn["orders"]), "without filter the same data buys")

# --- live/paper tick: total kill pauses (bot stays enabled), resumes after 14 days ---
os.environ["STATE_FILE"] = A.STATE = os.path.join(tmp, "state2.json")
clock = {"t": 1_800_000_000}
A.now_ts = lambda: clock["t"]
price["v"] = 100.0
write_cfg(risk=dict(C.DEFAULTS["risk"], stoploss_enabled=False, daily_loss_enabled=False))
A.tick(force=True)
s2 = json.load(open(os.environ["STATE_FILE"]))
check(s2["books"]["paper"]["position"], "paper bought")
price["v"] = 60.0; clock["t"] += 3600
A.tick()
s2 = json.load(open(os.environ["STATE_FILE"])); cfg2 = json.load(open(os.environ["BOT_CONFIG"]))
check(s2["pause"]["active"] and s2["status"] == "paused" and not s2["books"]["paper"]["position"], "paper kill -> pause")
check(cfg2["enabled"] is True and cfg2["mode"] == "paper" and not (s2.get("kill") or {}).get("active"), "bot not disabled by paper kill")
clock["t"] += 7 * 86400
A.tick(force=True)
s2 = json.load(open(os.environ["STATE_FILE"]))
check(not s2["books"]["paper"]["position"] and s2["status"] == "paused" and s2["log"][-1].get("blocked"), "no entry during pause")
clock["t"] += 7 * 86400
A.tick(force=True)
s2 = json.load(open(os.environ["STATE_FILE"]))
check(not s2["pause"]["active"] and s2["status"] == "active", "pause over -> active")
check(abs(s2["books"]["paper"]["loss_base"] - s2["books"]["paper"]["idr"] - s2["books"]["paper"]["sol"] * 60.0) < 1 or s2["books"]["paper"]["position"], "baseline reset")
check(s2["books"]["paper"]["position"], "re-entered after pause")
check(any(e["title"] == "Jeda kill switch selesai" for e in s2["events"]), "resume event")
# trend filter in the live decision: falling daily closes -> no new entry
os.environ["STATE_FILE"] = A.STATE = os.path.join(tmp, "state3.json")
saved_bars = A.fetch_bars
fall = [dict(t=1_700_000_000 + i * 86400, o=100.0, h=101.0, l=99.0, c=300.0 - i * 0.5) for i in range(320)]
fall[-12:] = [dict(fall[-12 + k], c=fall[-13]["c"] + 3 * k, h=fall[-13]["c"] + 3 * k + 1) for k in range(12)]  # short bounce, still < SMA200
A.fetch_bars = lambda tf, n=None, days=None, closed_only=True: fall
price["v"] = fall[-1]["c"]
write_cfg()
A.tick(force=True)
s3 = json.load(open(os.environ["STATE_FILE"]))
check(s3["log"][-1]["signal"] == "IN" and not s3["books"]["paper"]["position"] and "filter tren" in (s3["log"][-1].get("blocked") or ""),
      f"live MA filter blocks a Supertrend IN below SMA200: {s3['log'][-1].get('blocked')}")
check(s3["log"][-1]["trend"]["ok"] is False and s3["log"][-1]["trend"]["ma"] == 200, "trend info logged")
A.fetch_bars = saved_bars
print(f"OK: {ok} checks passed")
