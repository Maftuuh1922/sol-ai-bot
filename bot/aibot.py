#!/usr/bin/env python3
"""
SOL/IDR AI trading bot for Indodax (spot, long-or-cash, no leverage).

Every day after the daily candle closes (07:00 WIB), the bot:
  1. pulls SOL/IDR daily candles from Indodax,
  2. computes indicators (Supertrend 10/3, EMA50/200, RSI14, ATR%, volatility, returns),
  3. asks an LLM (chain: NafNaf -> Gonka -> Arcee, whichever keys are set)
     for a target exposure 0-100% with a reason (falls back to Supertrend if every LLM fails),
  4. clamps it with hard guardrails (vol-target cap, min rebalance step, spot only),
  5. trades a paper wallet (or real Indodax orders in live mode),
  6. runs a pure-Supertrend paper wallet beside it as a benchmark.

Usage:
  python3 aibot.py paper          # default; simulated wallets in state.json
  python3 aibot.py live           # real orders, needs INDODAX_KEY / INDODAX_SECRET env
  python3 aibot.py status         # print wallets and last decisions
  python3 aibot.py probe          # test every configured AI provider, no trading
No strategy guarantees profit.
"""
import json, os, re, sys, time, hmac, hashlib, math, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

PAIR_TV, PAIR_API = "SOLIDR", "sol_idr"
START_IDR = 1_000_000.0
BUY_FEE, SELL_FEE = 0.002111, 0.004211   # Indodax IDR taker (incl. 0.21% PPh on sell), Aug 2026 schedule
VOL_TARGET = 0.60      # annualised vol budget: exposure cap = VOL_TARGET / realised vol
MAX_EXPOSURE = 1.0
MIN_STEP = 0.10        # ignore rebalances smaller than 10% of equity
NAFNAF_URL = "https://inference.nafnaf.ai/v1/chat/completions"
GONKA_URL = "https://proxy.gonkabroker.com/v1/chat/completions"
ARCEE_URL = "https://api.arcee.ai/api/v1/chat/completions"
LLM_MAX_TOKENS = 800          # default reply budget
GONKA_MAX_TOKENS = 2048       # GLM reasons first (separate "reasoning" field); 800 often ran out before the JSON
LLM_TIMEOUT = 90
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("STATE_FILE") or os.path.join(HERE, "..", "docs", "data", "state.json")
MAX_EQUITY_POINTS = 2000
WIB = timezone(timedelta(hours=7))


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "solbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def candles(days=420):
    now = int(time.time())
    rows = get(f"https://indodax.com/tradingview/history_v2?symbol={PAIR_TV}&tf=1D&from={now-days*86400}&to={now}")
    bars = [dict(t=r["Time"], o=float(r["Open"]), h=float(r["High"]), l=float(r["Low"]), c=float(r["Close"]))
            for r in rows if r["Time"] + 86400 <= now]          # closed candles only
    return bars


# ---------- indicators ----------
def ema(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(xs, n=14):
    g = l = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100 - 100 / (1 + g / l) if l else 100.0


def supertrend(bars, period=10, mult=3.0):
    n = len(bars); tr = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["c"]
        tr.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    atr = [None] * n
    for i in range(period - 1, n):
        atr[i] = sum(tr[:period]) / period if i == period - 1 else (atr[i - 1] * (period - 1) + tr[i]) / period
    up = [None] * n; dn = [None] * n; trend = [1] * n
    for i in range(n):
        if atr[i] is None: continue
        hl2 = (bars[i]["h"] + bars[i]["l"]) / 2
        bu, bd = hl2 - mult * atr[i], hl2 + mult * atr[i]
        if up[i - 1] is not None and bars[i - 1]["c"] > up[i - 1]: bu = max(bu, up[i - 1])
        if dn[i - 1] is not None and bars[i - 1]["c"] < dn[i - 1]: bd = min(bd, dn[i - 1])
        up[i], dn[i] = bu, bd
        t = trend[i - 1]
        if t == -1 and dn[i - 1] is not None and bars[i]["c"] > dn[i - 1]: t = 1
        elif t == 1 and up[i - 1] is not None and bars[i]["c"] < up[i - 1]: t = -1
        trend[i] = t
    return trend, up, dn, atr


def features(bars):
    c = [b["c"] for b in bars]
    trend, up, dn, atr = supertrend(bars)
    rets = [math.log(c[i] / c[i - 1]) for i in range(len(c) - 30, len(c))]
    mu = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(365)
    e50, e200 = ema(c, 50)[-1], ema(c, 200)[-1]
    flips = [i for i in range(len(trend) - 60, len(trend)) if trend[i] != trend[i - 1]]
    return {
        "date": datetime.fromtimestamp(bars[-1]["t"], timezone.utc).strftime("%Y-%m-%d"),
        "close_idr": c[-1],
        "supertrend": "UP" if trend[-1] == 1 else "DOWN",
        "supertrend_line_idr": round(up[-1] if trend[-1] == 1 else dn[-1]),
        "days_since_trend_flip": (len(trend) - 1 - flips[-1]) if flips else ">60",
        "ema50_idr": round(e50), "ema200_idr": round(e200),
        "rsi14": round(rsi(c), 1),
        "atr14_pct": round(100 * atr[-1] / c[-1], 2),
        "realized_vol_30d_annual_pct": round(100 * vol, 1),
        "ret_7d_pct": round(100 * (c[-1] / c[-8] - 1), 2),
        "ret_30d_pct": round(100 * (c[-1] / c[-31] - 1), 2),
        "ret_90d_pct": round(100 * (c[-1] / c[-91] - 1), 2),
        "drawdown_from_365d_high_pct": round(100 * (c[-1] / max(c[-365:]) - 1), 2),
        "last_10_closes_idr": [round(x) for x in c[-10:]],
        "_vol": vol, "_trend": trend[-1],
    }


# ---------- AI decision ----------
SYSTEM = """You are the risk-aware portfolio manager of a SOL/IDR spot bot on Indodax (no leverage, no shorting).
Once per day you choose target_exposure: the fraction of equity (0.0-1.0) to hold in SOL; the rest stays in IDR.
Evidence you should respect: daily trend-following/time-series momentum is the best-supported edge in crypto;
mean reversion and frequent trading usually lose to fees (round trip ~0.63% here); big drawdowns come from
staying long in downtrends. Prefer few, decisive changes. Never chase a single green day.
Reply with ONLY a JSON object: {"target_exposure": number, "confidence": "low|medium|high", "reason": "max 2 sentences, Bahasa Indonesia"}"""


def ask_ai(feat, exposure_now, history):
    user = {"market": {k: v for k, v in feat.items() if not k.startswith("_")},
            "current_exposure": round(exposure_now, 3),
            "your_last_decisions": history[-5:]}
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(user)}]
    errors = []
    for name, url, key, model, max_tokens in providers():
        try:
            d = call_llm(url, key, model, msgs, max_tokens)
            d["provider"] = f"{name}:{model}"
            return d, errors
        except Exception as e:
            errors.append(f"{name}:{model} -> {describe_err(e)}")
    raise RuntimeError(" | ".join(errors) or "no LLM provider configured")


def providers():
    """Ordered chain of OpenAI-compatible providers; each is used only if its key is set.
    (GitHub Models was removed: retired July 30 2026.)"""
    env = os.environ.get
    chain = [
        ("nafnaf", NAFNAF_URL, "NAFNAF_API_KEY", env("NAFNAF_MODEL") or "qwen3.8-flash-next", LLM_MAX_TOKENS),
        ("gonka", GONKA_URL, "GONKA_API_KEY", env("GONKA_MODEL") or "zai-org/GLM-5.3-Flash", GONKA_MAX_TOKENS),
        ("arcee", ARCEE_URL, "ARCEE_API_KEY", env("ARCEE_MODEL") or "deepseek/deepseek-v4-flash-latest", LLM_MAX_TOKENS),
    ]
    return [(n, u, env(k).strip(), m, mt) for n, u, k, m, mt in chain if (env(k) or "").strip()]


def describe_err(e):
    if isinstance(e, urllib.error.HTTPError):
        try: body = e.read().decode(errors="replace")[:300]
        except Exception: body = ""
        return f"HTTP {e.code} {body}".strip()
    return f"{type(e).__name__}: {e}"[:300]


def extract_json(txt):
    """Pull the first JSON object out of an LLM reply (handles <think> blocks, code fences, padding)."""
    t = (txt or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S | re.I)
    t = re.sub(r"^.*?</think>", "", t, flags=re.S | re.I)          # unterminated-open variant
    t = re.sub(r"```(?:json)?", "", t, flags=re.I).strip()
    start = t.find("{")
    if start < 0: raise ValueError(f"tidak ada JSON di jawaban: {t[:150]!r}")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        elif ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0: return json.loads(t[start:i + 1])
    raise ValueError(f"JSON tidak lengkap: {t[start:start+150]!r}")


def call_llm(url, key, model, msgs, max_tokens=LLM_MAX_TOKENS):
    body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": max_tokens, "messages": msgs}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": f"Bearer {key}", "User-Agent": "sol-ai-bot/1.0"})
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
        raw = r.read().decode(errors="replace")
        status_code = r.status
    try:
        choice = json.loads(raw)["choices"][0]; msg = choice["message"]
    except Exception:
        raise RuntimeError(f"HTTP {status_code} respons bukan JSON chat: {raw[:200]!r}")
    txt = msg.get("content") or ""
    if not txt.strip() and choice.get("finish_reason") == "length":
        raise RuntimeError(f"jawaban kosong: token habis untuk reasoning (max_tokens={max_tokens})")
    try:
        d = extract_json(txt)
    except ValueError:
        # some reasoning models leave content empty and put everything in a reasoning field
        alt = msg.get("reasoning") or msg.get("reasoning_content") or ""
        if not alt: raise
        d = extract_json(alt)
    d["target_exposure"] = max(0.0, min(1.0, float(d["target_exposure"])))
    if d.get("confidence") not in ("low", "medium", "high"): d["confidence"] = "medium"
    d["reason"] = str(d.get("reason", ""))[:400]
    return d


def guard(target, feat):
    cap = min(MAX_EXPOSURE, VOL_TARGET / max(feat["_vol"], 1e-6))
    return max(0.0, min(target, cap)), round(cap, 3)


# ---------- wallets ----------
def rebalance(w, target, px, live=False):
    eq = w["idr"] + w["sol"] * px
    cur = w["sol"] * px / eq if eq else 0
    if abs(target - cur) < MIN_STEP and not (target == 0 and w["sol"] > 0 and cur < MIN_STEP):
        return "HOLD"
    delta_idr = (target - cur) * eq
    if delta_idr > 0:
        spend = min(delta_idr, w["idr"])
        if live: indodax_trade("buy", px, idr=spend)
        w["sol"] += spend * (1 - BUY_FEE) / px; w["idr"] -= spend
        return f"BUY Rp{spend:,.0f}"
    sell_sol = min(-delta_idr / px, w["sol"])
    if target == 0: sell_sol = w["sol"]
    if live: indodax_trade("sell", px, sol=sell_sol)
    w["idr"] += sell_sol * px * (1 - SELL_FEE); w["sol"] -= sell_sol
    return f"SELL {sell_sol:.4f} SOL"


def indodax_trade(side, px, idr=None, sol=None):
    key, sec = os.environ["INDODAX_KEY"], os.environ["INDODAX_SECRET"]
    p = {"method": "trade", "pair": PAIR_API, "type": side, "order_type": "market",
         "nonce": int(time.time() * 1000), "price": int(px)}
    if idr: p["idr"] = int(idr)
    if sol: p["sol"] = f"{sol:.6f}"
    body = urllib.parse.urlencode(p).encode()
    sign = hmac.new(sec.encode(), body, hashlib.sha512).hexdigest()
    req = urllib.request.Request("https://indodax.com/tapi", data=body, method="POST",
                                 headers={"Key": key, "Sign": sign})
    res = json.load(urllib.request.urlopen(req, timeout=30))
    if res.get("success") != 1: raise RuntimeError(f"Indodax error: {res}")
    return res


def load():
    s = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if not s.get("ai"):
        s.update({"ai": {"idr": START_IDR, "sol": 0.0}, "bench": {"idr": START_IDR, "sol": 0.0},
                  "hold": {"idr": 0.0, "sol": None}, "last_date": None, "log": []})
    s.setdefault("equity", [])
    s["start_idr"] = START_IDR
    return s


def save(s):
    os.makedirs(os.path.dirname(os.path.abspath(STATE)), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(s, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE)


def eqv(w, px): return w["idr"] + (w["sol"] or 0) * px


def run(live=False):
    bars = candles(); feat = features(bars); px = feat["close_idr"]
    s = load()
    if s["last_date"] == feat["date"]:
        print(f"Sudah diproses untuk candle {feat['date']}."); return status(s, px)
    if s["hold"]["sol"] is None: s["hold"]["sol"] = START_IDR * (1 - BUY_FEE) / px
    eq = s["ai"]["idr"] + s["ai"]["sol"] * px
    exp_now = s["ai"]["sol"] * px / eq
    hist = [{"date": e["date"], "target": e["final"], "reason": e["reason"]} for e in s["log"]]
    try:
        d, soft = ask_ai(feat, exp_now, hist); err = " | ".join(soft) or None
    except Exception as e:  # AI down -> fall back to Supertrend rule
        d = {"target_exposure": 1.0 if feat["_trend"] == 1 else 0.0, "confidence": "fallback",
             "provider": "fallback:supertrend",
             "reason": "AI tidak merespons; pakai aturan Supertrend sebagai cadangan."}; err = str(e)
        print("AI gagal:", err, file=sys.stderr)
    final, cap = guard(d["target_exposure"], feat)
    act = rebalance(s["ai"], final, px, live)
    bact = rebalance(s["bench"], 1.0 if feat["_trend"] == 1 else 0.0, px)   # pure Supertrend benchmark
    s["last_date"] = feat["date"]
    s["log"].append({"date": feat["date"], "close": px, "ai_target": round(d["target_exposure"], 3), "cap": cap,
                     "final": round(final, 3), "confidence": d.get("confidence"), "reason": d.get("reason"),
                     "provider": d.get("provider"), "action": act, "bench_action": bact,
                     "supertrend": feat["supertrend"], "error": err,
                     "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    s["equity"] = [p for p in s["equity"] if p["date"] != feat["date"]]
    s["equity"].append({"date": feat["date"], "close": px, "ai": round(eqv(s["ai"], px)),
                        "bench": round(eqv(s["bench"], px)), "hold": round(eqv(s["hold"], px))})
    s["equity"] = s["equity"][-MAX_EQUITY_POINTS:]
    s["mode"] = "LIVE" if live else "PAPER"
    s["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s["market"] = {k: v for k, v in feat.items() if not k.startswith("_")}
    s["exposure"] = {"ai": round(s["ai"]["sol"] * px / max(eqv(s["ai"], px), 1e-9), 3),
                     "bench": round(s["bench"]["sol"] * px / max(eqv(s["bench"], px), 1e-9), 3)}
    save(s)
    status(s, px)


def status(s=None, px=None):
    s = s or load()
    if px is None: px = float(get("https://indodax.com/api/ticker/solidr")["ticker"]["last"])
    eq = lambda w: eqv(w, px)
    out = {"harga_SOL_IDR": px,
           "AI": round(eq(s["ai"])), "Supertrend": round(eq(s["bench"])), "Hold": round(eq(s["hold"])) if s["hold"]["sol"] else None,
           "modal_awal": START_IDR, "keputusan_terakhir": s["log"][-1] if s["log"] else None}
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    mode = (sys.argv[1] if len(sys.argv) > 1 else "paper").lower()
    if mode == "status": status()
    elif mode == "probe":   # cek penyedia AI tanpa trading / tanpa ubah state
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(
            {"market": {"supertrend": "UP", "rsi14": 55}, "current_exposure": 0, "your_last_decisions": []})}]
        ok = False
        for name, url, key, model, max_tokens in providers():
            t0 = time.time()
            try:
                d = call_llm(url, key, model, msgs, max_tokens)
                print(f"OK  {name}:{model} ({time.time()-t0:.1f}s) -> {d}"); ok = True
            except Exception as e: print(f"ERR {name}:{model} ({time.time()-t0:.1f}s) -> {describe_err(e)}")
        if not providers(): print("Tidak ada penyedia AI yang dikonfigurasi.")
        sys.exit(0 if ok else 1)
    elif mode in ("paper", "live"):
        if mode == "live" and not (os.environ.get("INDODAX_KEY") and os.environ.get("INDODAX_SECRET")):
            sys.exit("Mode live butuh secret INDODAX_KEY dan INDODAX_SECRET.")
        run(live=(mode == "live"))
    else: sys.exit("Pakai: aibot.py paper|live|status|probe")
