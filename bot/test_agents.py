"""Offline tests for the multi-agent layer + Hyperliquid PAPER engine: python3 bot/test_agents.py
No network, no AI, never live."""
import json, os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
os.environ["STATE_FILE"] = os.path.join(tmp, "state.json")
os.environ["BOT_CONFIG"] = os.path.join(tmp, "cfg.json")
for k in ("NAFNAF_API_KEY", "GONKA_API_KEY", "ARCEE_API_KEY", "INDODAX_API_KEY", "INDODAX_KEY", "INDODAX_SECRET", "BOT_MODE"):
    os.environ.pop(k, None)
import agents as AG, hyperliquid as HL, perps as PERPS, config as C, aibot as A  # noqa: E402

ok = 0
def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1

P = json.loads(json.dumps(C.DEFAULTS["perps"]))

# --- config ---
cfg, w = C.validate({"perps": {"max_leverage": 20, "risk_per_trade_pct": 9, "pairs": "sol, btc-perp, ETH, sol, $$$"}})
check(cfg["perps"]["max_leverage"] == 5, "leverage hard cap 5x")
check(cfg["perps"]["risk_per_trade_pct"] == 5, "risk clamp")
check(cfg["perps"]["pairs"] == ["SOL", "BTC", "ETH"], f"pairs parsed {cfg['perps']['pairs']}")
check(C.validate({})[0]["perps"]["max_leverage"] == 3 and C.validate({})[0]["perps"]["paper_start_usd"] == 250, "defaults")
check(C.validate({"venue": "moon"})[0]["venue"] == "both", "bad venue -> both")

# --- features / rule agents on a synthetic uptrend ---
bars = [dict(t=1_700_000_000 + i * 14400, o=100 + i * 0.3, h=101 + i * 0.3, l=99.5 + i * 0.3, c=100.5 + i * 0.3, v=1000 + (i % 7) * 50) for i in range(320)]
f = AG.features(bars, {"funding": 0.0000125, "premium": 0.0, "mark": 196.0, "prev_day_px": 194.0}, {"value": 50, "prev": 48},
                {"imbalance": 0.1, "spread_bps": 1.0, "depth_usd_1pct": 1e6}, 1.0)
rv = AG.rule_votes(f)
check(set(rv) == set(AG.AGENTS), "six agents")
check(all(v["dir"] in AG.DIRS and 0 <= v["conf"] <= 1 and v["reason"] for v in rv.values()), "vote schema")
check(rv["trend"]["dir"] == "long", "trend agent long in uptrend")
co = AG.coordinate(rv, 0.25, True)
check(co["direction"] in ("long", "flat") and co["score"] > 0, f"coordinator positive {co}")
check(AG.coordinate({a: AG.vote("short", 1, "x") for a in AG.AGENTS}, 0.25, False)["direction"] == "flat", "no shorts when disabled")
# --- funding moderate + regime transisi: arahnya harus konsisten, bukan kebalik ---
# Funding moderat positif (APR ~17%) searah dengan ekstrem positif (short), bukan long.
check(AG.a_funding({"funding_hourly_pct": 0.002, "funding_apr_pct": 17.5})["dir"] == "short", "funding moderat positif -> short (bukan long)")
check(AG.a_funding({"funding_hourly_pct": -0.001, "funding_apr_pct": -8.8})["dir"] == "long", "funding moderat negatif -> long (bukan short)")
# Rezim transisi (ER 0.2-0.35) bukan tren: vote flat, jangan paksa arah di chop.
check(AG.a_regime({"efficiency_ratio": 0.30, "atr_pct": 1.0, "atr_pct_median100": 1.0, "roc20_pct": 5.0})["dir"] == "flat", "rezim transisi -> flat")

# --- LLM parse + fallback ---
good = {"coins": {"SOL": {a: {"dir": "short", "conf": 70, "reason": "r"} for a in AG.AGENTS}}}
pv = AG.parse_llm_votes(good, ["SOL"])
check(pv["SOL"]["trend"]["conf"] == 0.7, "conf 70 -> 0.7")
votes, src, err = AG.analyst_votes({"SOL": f}, lambda s, p: (good, "Fake · m"))
check(src == "Fake · m" and votes["SOL"]["trend"]["source"] == "llm", "llm votes used")
def boom(s, p): raise RuntimeError("down")
votes, src, err = AG.analyst_votes({"SOL": f}, boom)
check(src.startswith("Cadangan") and votes["SOL"]["trend"]["source"] == "rule" and "down" in err, "rule fallback")
votes, src, err = AG.analyst_votes({"SOL": f}, lambda s, p: ({"coins": {"SOL": {"trend": {"dir": "long", "conf": 0.9, "reason": "x"}}}}, "F"))
check(votes["SOL"]["momentum"]["source"] == "rule" and err, "partial llm -> rest rule")

# --- risk agent ---
bs = {"equity": 250.0, "free_margin": 250.0, "open_positions": 0, "has_position": False, "daily_loss_pct": 0, "total_loss_pct": 0,
      "kill_active": False, "daily_block": None}
fx = dict(f, close=100.0, atr=2.0)
long = {"direction": "long", "score": 0.5}
r = AG.risk_agent("SOL", long, fx, P, bs, 20)
pl = r["plan"]
check(r["approved"] and pl["side"] == "long", "approved long")
check(abs(pl["stop"] - 96.0) < 1e-9 and abs(pl["tp"] - 108.0) < 1e-9, "stop 2xATR, TP 2R written before entry")
check(abs(pl["risk_usd"] - 2.5) < 0.01, f"risk = 1% of equity ({pl['risk_usd']})")
check(1 <= pl["leverage"] <= 3, "leverage within max")
check(abs(100 - pl["liq"]) >= 2 * 4.0 or pl["liq"] == 0, "liq buffer")
check(not AG.risk_agent("SOL", {"direction": "flat", "score": 0}, fx, P, bs)["approved"], "flat veto")
check("stop" in AG.risk_agent("SOL", long, dict(fx, atr=None), P, bs)["veto"].lower(), "no ATR -> no stop -> veto")
check("Kill" in AG.risk_agent("SOL", long, fx, P, dict(bs, kill_active=True))["veto"], "kill veto")
check("harian" in AG.risk_agent("SOL", long, fx, P, dict(bs, daily_loss_pct=3.5))["veto"], "daily veto")
check("Maks" in AG.risk_agent("SOL", long, fx, P, dict(bs, open_positions=2))["veto"], "max positions veto")
check("minimum" in AG.risk_agent("SOL", long, fx, P, dict(bs, equity=20, free_margin=20))["veto"], "min notional veto")
small = AG.risk_agent("SOL", long, fx, P, dict(bs, free_margin=30))
check(small["approved"] and small["plan"]["leverage"] <= 3 and small["plan"]["notional"] <= 90.01, "margin-limited size at <=3x")
tight = dict(P, max_leverage=5, liq_buffer_mult=8, stop_atr_mult=2)
rt = AG.risk_agent("SOL", {"direction": "short", "score": -0.5}, dict(fx, atr=4.0), tight, dict(bs, free_margin=60), 20)
check((not rt["approved"]) or abs(rt["plan"]["liq"] - 100) >= 8 * 8.0, "liq buffer enforced (short)")

# --- paper engine ---
b = HL.new_book(250)
try: HL.open_position(b, "SOL", dict(pl, stop=None), 100.0, 0, "x"); check(False, "must refuse")
except ValueError: check(True, "refuse trade without stop")
try: HL.open_position(b, "SOL", dict(pl, stop=101.0), 100.0, 0, "x"); check(False, "must refuse")
except ValueError: check(True, "refuse stop on wrong side")
p = HL.open_position(b, "SOL", pl, 100.0, 0, "x")
check(b["balance"] < 250 and b["fees_paid"] > 0, "entry fee charged")
paid = HL.apply_funding(b, "SOL", 0.0001, 100.0, 2)
check(paid > 0 and p["funding"] > 0, "long pays positive funding")
check(HL.check_exit(p, 99.0, 101.0) is None, "no exit inside range")
check(HL.check_exit(p, 95.0, 109.0)[0] == "stoploss", "stop first when both hit")
check(HL.check_exit(p, 99.0, 108.5)[0] == "take_profit", "take profit")
check(HL.maybe_breakeven(p, 99, 104.5, 1.0) and p["stop"] > p["entry"], "breakeven move after +1R")
t = HL.close_position(b, "SOL", 108.0, 10, "take_profit")
check(t["pnl_usd"] > 0 and t["r_multiple"] > 1 and not b["positions"], "closed with profit")
liq = HL.liq_price("long", 100, 3, HL.maint_rate(20))
check(65 < liq < 70, f"3x long liq ~68 ({liq})")
check(HL.liq_price("short", 100, 3, HL.maint_rate(20)) > 130, "3x short liq")
p2 = HL.open_position(b, "ETH", dict(pl, leverage=3, liq=liq), 100.0, 0, "x")
t2 = HL.close_position(b, "ETH", liq, 5, "liquidation", liquidated=True)
check(abs(t2["pnl_usd"] + p2["margin"] + p2["fees"] + p2["funding"]) < 1e-3, "liquidation loses the margin")
try: HL.live_order("SOL", "buy"); check(False, "stub must raise")
except RuntimeError: check(True, "live stub disabled")

# --- no live Hyperliquid code path anywhere ---
src = "".join(open(os.path.join(os.path.dirname(__file__), n)).read() for n in ("hyperliquid.py", "perps.py", "agents.py", "aibot.py"))
check("/exchange" not in src and "eth_account" not in src and "sign_l1_action" not in src, "no exchange endpoint / signing")

# --- perps tick with a fake market (offline) ---
ctx = {c: {"mark": px, "mid": px, "funding": 0.00001, "oi": 1000.0, "oi_usd": 1e6, "premium": 0.0, "max_leverage": 20,
           "prev_day_px": px * 0.99, "day_volume_usd": 1e8} for c, px in (("SOL", 196.0), ("BTC", 196.0), ("ETH", 196.0))}
HL.market_ctx = lambda: ctx
HL.closed_candles = lambda coin, tf, n: bars
HL.candles = lambda coin, tf, a, b2=None: []
HL.l2_book = lambda coin: ([(195.9, 100.0)], [(196.1, 100.0)])
events = []
def ev(s, typ, title, body, mode=None, dedupe_hours=0): events.append((typ, title)); return {}
s = {"events": []}
cfg = C.validate({})[0]; cfg["ai"]["enabled"] = False
PERPS.tick(cfg, s, 1_800_000_000, True, None, ev, A.iso, lambda u, timeout=15: {"data": [{"value": "50"}, {"value": "49"}]})
ps = s["perps"]
check(set(ps["latest"]) == {"SOL", "BTC", "ETH"}, "decision per pair")
check(all(len(d["votes"]) == 6 for d in ps["latest"].values()), "six votes stored per pair")
check(len(ps["book"]["positions"]) <= 2, "max 2 positions")
for c, p in ps["book"]["positions"].items():
    check(p["stop"] and p["tp"] and p["liq"] is not None and p["leverage"] <= 3, f"{c} has stop/tp/liq")
check(any(t == "agent" for t, _ in events), "agent event logged")
n_open = len(ps["book"]["positions"])
PERPS.tick(cfg, s, 1_800_000_000 + 900, False, None, ev, A.iso, lambda u, timeout=15: None, close_request=True)
check(not ps["book"]["positions"] and len(ps["book"]["trades"]) == n_open, "manual close closes perps")
cfg["perps"]["reset_request"] = "r1"
PERPS.tick(cfg, s, 1_800_000_000 + 1800, False, None, ev, A.iso, lambda u, timeout=15: None)
check(not s["perps"]["book"]["trades"] and s["perps"]["reset_done"] == "r1", "reset works")
# --- full aibot.tick with venue=both (Indodax + Hyperliquid), all offline ---
A.ticker = lambda: 100.0
ib = [dict(t=1_700_000_000 + i * 86400, o=100.0, h=101.0, l=99.0, c=100.0 + i * 0.5) for i in range(320)]
A.fetch_bars = lambda tf, n=None, days=None, closed_only=True: ib
A.get = lambda url, timeout=30: {"data": [{"value": "50"}, {"value": "49"}]}
c = json.loads(json.dumps(C.DEFAULTS)); c["venue"] = "both"; json.dump(c, open(os.environ["BOT_CONFIG"], "w"))
A.tick(force=True)
st = json.load(open(os.environ["STATE_FILE"]))
check(st["venue"] == "both" and st["books"]["paper"]["position"] and st["perps"]["latest"], "both venues ran")
check(st["mode"] == "PAPER" and st["perps"]["mode"] == "PAPER", "paper everywhere")
c["venue"] = "hyperliquid"; json.dump(c, open(os.environ["BOT_CONFIG"], "w"))
n_ord = len(st["books"]["paper"]["orders"])
A.tick(force=True)
st = json.load(open(os.environ["STATE_FILE"]))
check(len(st["books"]["paper"]["orders"]) == n_ord, "hyperliquid-only leaves Indodax untouched")

# --- perps kill switch: pause 14 days then resume with a new baseline ---
kb = HL.new_book(250)
PERPS.kill_start(kb, dict(P, kill_pause_days=14), 1000, 16.0)
check(kb["kill"]["until_ts"] == 1000 + 14 * 86400, "perps pause until")
check(not PERPS.kill_resume(kb, {}, 1000 + 14 * 86400 - 1) and kb["kill"], "perps still paused")
kb["balance"] = 200.0
check(PERPS.kill_resume(kb, {}, 1000 + 14 * 86400) and kb["kill"] is None and kb["loss_base"] == 200.0, "perps resume + baseline reset")
check(HL.loss_state(kb, {})[1] == 0.0, "no instant re-trigger after resume")
kb2 = HL.new_book(250); PERPS.kill_start(kb2, dict(P, kill_pause_days=0), 1000, 16.0)
check(kb2["kill"]["until_ts"] is None and not PERPS.kill_resume(kb2, {}, 10**10), "perps pause 0 = until reset")
check(C.DEFAULTS["perps"]["kill_pause_days"] == 14, "perps pause default")
bv = PERPS.book_view({"book": dict(HL.new_book(250), kill={"at": 1, "until_ts": 2})}, {})
check(AG.risk_agent("SOL", {"direction": "long", "score": 0.5}, {"close": 100, "atr": 2}, P, dict(bv, has_position=False))["veto"] == "Kill switch aktif", "veto while paused")
print(f"OK: {ok} checks passed")
