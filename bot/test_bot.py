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
    c = json.loads(json.dumps(C.DEFAULTS)); c.update(kw); json.dump(c, open(os.environ["BOT_CONFIG"], "w"))
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
print(f"OK: {ok} checks passed")
