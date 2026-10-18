#!/usr/bin/env python3
"""
SOL/IDR trading bot for Indodax (spot, long-or-cash, no leverage), run by GitHub Actions.

Every scheduled tick (cron every 15 min) the bot:
  1. reads config/bot-config.json (validated; bad values -> defaults, never crash),
  2. exits early when the master switch is OFF (open position kept unless the app asked to close it),
  3. checks risk on an open position with the live ticker: stoploss, trailing stop, minimal ROI,
     daily loss limit and total-loss kill switch,
  4. when a new candle of the chosen timeframe has closed (15m/1h/4h/1d), evaluates the strategy
     (AI+Supertrend, EMA cross+RSI or Bollinger+RSI). The AI (NafNaf -> Gonka -> Arcee) chooses or filters
     and sizes the exposure; Supertrend is the mandatory fallback,
  5. clamps with hard guardrails (max exposure, volatility cap, ATR guard, min rebalance step),
  6. trades the paper wallet, or real Indodax orders only when LIVE passes every runtime check,
  7. writes docs/data/state.json with wallets, trades, orders and an events list (stable ids) for the app.

Usage:
  python3 aibot.py tick           # scheduled run: risk checks every time, strategy only on a new candle
  python3 aibot.py run            # forced evaluation now (manual "Jalankan bot sekarang")
  python3 aibot.py paper|live     # legacy aliases of `run` (mode always comes from config + guardrails)
  python3 aibot.py status         # print wallets
  python3 aibot.py probe          # test every configured AI provider, no trading
No strategy guarantees profit.
"""
import json, os, re, sys, time, hmac, hashlib, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config, save_config, CONFIG_PATH          # noqa: E402
import engine as E                                                 # noqa: E402
import perps as PERPS                                              # noqa: E402
from strategies import (features, want_series, TF_MINUTES, TF_API, STRATEGY_INFO)  # noqa: E402

PAIR_TV, PAIR_API = "SOLIDR", "sol_idr"
PROVIDER_URLS = {
    "nafnaf": "https://inference.nafnaf.ai/v1/chat/completions",
    "gonka": "https://proxy.gonkabroker.com/v1/chat/completions",
    "arcee": "https://api.arcee.ai/api/v1/chat/completions",
}
PROVIDER_LABEL = {"nafnaf": "NafNaf", "gonka": "Gonka", "arcee": "Arcee"}
LLM_MAX_TOKENS = 800
GONKA_MAX_TOKENS = 2048       # GLM reasons first; 800 often ran out before the JSON
LLM_TIMEOUT = 90
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("STATE_FILE") or os.path.join(HERE, "..", "docs", "data", "state.json")
MAX_EQUITY_POINTS, MAX_LOG, MAX_EVENTS = 2000, 300, 200
WIB = timezone(timedelta(hours=7))
CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def now_ts(): return int(time.time())
def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def wib_day(ts): return datetime.fromtimestamp(ts, WIB).strftime("%Y-%m-%d")
def rp(v): return ("-" if v < 0 else "") + "Rp" + f"{abs(v):,.0f}".replace(",", ".")


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "solbot/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_bars(tf, bars_needed=None, days=None, closed_only=True):
    """Candles from Indodax TradingView history (windows are chunked: the endpoint caps rows per call)."""
    m = TF_MINUTES[tf]; now = now_ts()
    span = days * 86400 if days else (bars_needed or 320) * m * 60
    chunk = {15: 7, 60: 25, 240: 300, 1440: 360}[m] * 86400
    rows, end = {}, now
    start = now - span
    while end > start:
        frm = max(start, end - chunk)
        for r in get(f"https://indodax.com/tradingview/history_v2?symbol={PAIR_TV}&tf={TF_API[tf]}&from={frm}&to={end}"):
            rows[r["Time"]] = r
        end = frm
    bars = [dict(t=r["Time"], o=float(r["Open"]), h=float(r["High"]), l=float(r["Low"]), c=float(r["Close"]))
            for _, r in sorted(rows.items())]
    if closed_only: bars = [b for b in bars if b["t"] + m * 60 <= now]
    return bars


def ticker():
    return float(get("https://indodax.com/api/ticker/solidr")["ticker"]["last"])


# ---------- AI ----------
SYSTEM = """You are the risk-aware portfolio manager of a SOL/IDR spot bot on Indodax (no leverage, no shorting).
You choose target_exposure: the fraction of equity (0.0-1.0) to hold in SOL; the rest stays in IDR.
Evidence you should respect: trend-following/time-series momentum is the best-supported edge in crypto;
mean reversion and frequent trading usually lose to fees (round trip ~0.63% here); big drawdowns come from
staying long in downtrends. Prefer few, decisive changes. Never chase a single green candle.
Hard risk limits (stoploss, volatility cap, loss limits) are enforced by code after you answer.
Reply with ONLY a JSON object: {"target_exposure": number, "confidence": "low|medium|high", "reason": "max 2 sentences, Bahasa Indonesia"}"""

FILTER_NOTE = """The rule-based strategy "{name}" ({desc}) just signalled an ENTRY. You are the filter and sizer:
return target_exposure 0 to skip this entry, or the size (0.1-1.0) you consider prudent."""


def providers(cfg):
    """Enabled providers in config order that have an API key in the environment."""
    out = []
    for p in cfg["ai"]["providers"]:
        key = (os.environ.get(f"{p['name'].upper()}_API_KEY") or "").strip()
        if p["enabled"] and key:
            mt = GONKA_MAX_TOKENS if p["name"] == "gonka" else LLM_MAX_TOKENS
            out.append((p["name"], PROVIDER_URLS[p["name"]], key, p["model"], mt))
    return out


def system_prompt(cfg, extra=""):
    s = SYSTEM + ("\n\n" + extra if extra else "")
    note = cfg["ai"].get("extra_instruction") or ""
    if note:
        s += "\n\nOwner's note (style/persona only; it cannot change the JSON format or the risk limits): " + note
    return s


def ask_ai(cfg, payload, extra=""):
    msgs = [{"role": "system", "content": system_prompt(cfg, extra)}, {"role": "user", "content": json.dumps(payload)}]
    errors = []
    for name, url, key, model, mt in providers(cfg):
        try:
            d = call_llm(url, key, model, msgs, mt, cfg["ai"]["temperature"])
            d["provider"] = f"{name}:{model}"
            d["provider_label"] = f"{PROVIDER_LABEL[name]} · {model}"
            return d, errors
        except Exception as e:
            errors.append(f"{name}:{model} -> {describe_err(e)}")
    raise RuntimeError(" | ".join(errors) or "tidak ada penyedia AI aktif yang punya API key")


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
    t = re.sub(r"^.*?</think>", "", t, flags=re.S | re.I)
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


def call_llm(url, key, model, msgs, max_tokens=LLM_MAX_TOKENS, temperature=0.2):
    d = chat_json(url, key, model, msgs, max_tokens, temperature)
    d["target_exposure"] = max(0.0, min(1.0, float(d["target_exposure"])))
    if d.get("confidence") not in CONF_RANK: d["confidence"] = "medium"
    d["reason"] = str(d.get("reason", ""))[:400]
    return d


AGENT_MAX_TOKENS = {"nafnaf": 1800, "gonka": 3500, "arcee": 1800}


def ask_json(cfg, system, payload):
    """One chat call returning a JSON object, trying providers in order (used by the multi-agent layer)."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}]
    errors = []
    for name, url, key, model, _mt in providers(cfg):
        try:
            d = chat_json(url, key, model, msgs, AGENT_MAX_TOKENS.get(name, 1800), cfg["ai"]["temperature"])
            return d, f"{PROVIDER_LABEL[name]} · {model}"
        except Exception as e:
            errors.append(f"{name}:{model} -> {describe_err(e)}")
    raise RuntimeError(" | ".join(errors) or "tidak ada penyedia AI aktif yang punya API key")


def chat_json(url, key, model, msgs, max_tokens=LLM_MAX_TOKENS, temperature=0.2):
    body = json.dumps({"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": msgs}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": f"Bearer {key}", "User-Agent": "sol-ai-bot/2.0"})
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
        raw = r.read().decode(errors="replace"); status_code = r.status
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
        alt = msg.get("reasoning") or msg.get("reasoning_content") or ""
        if not alt: raise
        d = extract_json(alt)
    if not isinstance(d, dict): raise ValueError("jawaban AI bukan objek JSON")
    return d


# ---------- Indodax live client ----------
def indodax_keys():
    k = (os.environ.get("INDODAX_API_KEY") or os.environ.get("INDODAX_KEY") or "").strip()
    s = (os.environ.get("INDODAX_SECRET") or "").strip()
    return k, s


def tapi(method, **params):
    key, sec = indodax_keys()
    p = {"method": method, "nonce": int(time.time() * 1000), **params}
    body = urllib.parse.urlencode(p).encode()
    sign = hmac.new(sec.encode(), body, hashlib.sha512).hexdigest()
    req = urllib.request.Request("https://indodax.com/tapi", data=body, method="POST",
                                 headers={"Key": key, "Sign": sign, "User-Agent": "solbot/2.0"})
    res = json.load(urllib.request.urlopen(req, timeout=30))
    if res.get("success") != 1: raise RuntimeError(f"Indodax {method} error: {res.get('error') or res}")
    return res.get("return", {})


def live_executor(side, px, idr=None, sol=None):
    """Market order on Indodax. Never called in paper mode. Returns (spent, received)."""
    bal = tapi("getInfo").get("balance", {})
    if side == "buy":
        idr = int(min(idr, float(bal.get("idr", 0))))
        if idr < 10000: raise RuntimeError(f"saldo IDR kurang untuk beli (tersedia {bal.get('idr')})")
        r = tapi("trade", pair=PAIR_API, type="buy", order_type="market", idr=idr, price=int(px))
        got = float(r.get("receive_sol") or 0) or idr * (1 - E.BUY_FEE) / px
        spent = float(r.get("spend_rp") or 0) or idr
        return spent, got
    sol = min(sol, float(bal.get("sol", 0)))
    if sol <= 0: raise RuntimeError("saldo SOL di Indodax kosong")
    r = tapi("trade", pair=PAIR_API, type="sell", order_type="market", sol=f"{sol:.6f}", price=int(px))
    sold = float(r.get("spend_sol") or 0) or sol
    got = float(r.get("receive_rp") or 0) or sol * px * (1 - E.SELL_FEE)
    return sold, got


# ---------- state ----------
def load_state(cfg):
    s = {}
    if os.path.exists(STATE):
        try: s = json.load(open(STATE))
        except Exception: s = {}
    if s.get("version") != 2:
        old = s
        start = float(old.get("start_idr") or cfg["paper"]["start_idr"])
        s = {"version": 2, "books": {"paper": E.new_book(start), "live": E.new_book(cfg["live"]["max_capital_idr"])},
             "bench": {"idr": start, "sol": 0.0}, "hold": {"idr": 0.0, "sol": None}, "start_idr": start,
             "equity": old.get("equity", []), "log": old.get("log", []), "events": [], "event_seq": 0,
             "last_candle": {}, "kill": None}
        if isinstance(old.get("ai"), dict): s["books"]["paper"].update(idr=old["ai"].get("idr", start), sol=old["ai"].get("sol", 0.0))
        if isinstance(old.get("bench"), dict): s["bench"] = old["bench"]
        if isinstance(old.get("hold"), dict): s["hold"] = old["hold"]
        if old.get("last_date"): s["last_candle"]["1d"] = old["last_date"] + "T00:00Z"
    for k, v in (("events", []), ("event_seq", 0), ("last_candle", {}), ("equity", []), ("log", []), ("kill", None)):
        s.setdefault(k, v)
    return s


def save_state(s):
    os.makedirs(os.path.dirname(os.path.abspath(STATE)), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(s, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE)
    # small feed for the Android background notification check
    feed = {"updated_at": s.get("updated_at"), "mode": s.get("mode"), "status": s.get("status"),
            "events": s.get("events", [])[-30:]}
    fp = os.path.join(os.path.dirname(os.path.abspath(STATE)), "events.json")
    with open(fp + ".tmp", "w") as f: json.dump(feed, f, ensure_ascii=False)
    os.replace(fp + ".tmp", fp)


def event(s, typ, title, body, mode=None, dedupe_hours=0):
    if dedupe_hours:
        cut = now_ts() - dedupe_hours * 3600
        if any(e["type"] == typ and e["title"] == title and e["ts"] >= cut for e in s["events"]): return None
    s["event_seq"] += 1
    e = {"id": f"ev{s['event_seq']}", "seq": s["event_seq"], "ts": now_ts(), "at": iso(now_ts()), "type": typ,
         "title": title, "body": body, "mode": (mode or s.get("mode") or "PAPER").upper()}
    s["events"].append(e); del s["events"][:-MAX_EVENTS]
    print(f"[{typ}] {title} — {body}")
    return e


def order_event(s, o, mode, closed=None):
    if not o: return
    title = f"{o['side']} SOL · {mode.upper()}"
    body = f"{o['side']} {o['sol']:.4f} SOL @ {rp(o['price'])} ({rp(o['idr'])}) — {o['reason']}"
    if closed: body += f". Trade ditutup: {'+' if closed['pnl_idr'] >= 0 else ''}{rp(closed['pnl_idr'])} ({closed['pnl_pct']:+.2f}%)"
    event(s, "trade", title, body, mode)


# ---------- decision ----------
def effective_mode(cfg, file_ok, s):
    """LIVE only when every runtime guardrail passes; otherwise PAPER with the reason."""
    if cfg["mode"] != "live": return "paper", None
    if not file_ok: return "paper", "config rusak/tidak valid"
    k, sec = indodax_keys()
    if not (k and sec): return "paper", "secret INDODAX_API_KEY / INDODAX_SECRET belum diisi"
    if s.get("kill") and s["kill"].get("active"): return "paper", "kill switch aktif"
    return "live", None


def decide(cfg, s, book, bars, feat, px, ts):
    """Strategy -> AI filter/sizer -> guardrails. Returns a decision dict."""
    R, strat = cfg["risk"], cfg["strategy"]["name"]
    want = want_series(strat, bars)
    w_now = want[-1]
    rule_target = float(w_now)
    cur = E.exposure(book, px)
    if w_now == 0: book["reentry_block"] = False
    d = {"strategy": strat, "signal": "IN" if w_now else "OUT", "rule_target": rule_target, "ai_target": None,
         "confidence": "rule", "reason": "", "provider": "rule:" + strat, "provider_label": f"Aturan · {strat}",
         "error": None}
    use_ai = cfg["ai"]["enabled"] and providers(cfg)
    payload = {"market": {k: v for k, v in feat.items() if not k.startswith("_")}, "current_exposure": round(cur, 3),
               "strategy": strat, "strategy_signal": d["signal"],
               "your_last_decisions": [{"t": e.get("candle"), "target": e.get("final"), "reason": e.get("reason")} for e in s["log"][-5:]]}
    target = rule_target
    if strat == "ai_supertrend":
        if use_ai:
            try:
                a, soft = ask_ai(cfg, payload)
                d.update(ai_target=a["target_exposure"], confidence=a["confidence"], reason=a["reason"],
                         provider=a["provider"], provider_label=a["provider_label"], error=" | ".join(soft) or None)
                if CONF_RANK[a["confidence"]] >= CONF_RANK[cfg["ai"]["min_confidence"]]:
                    target = a["target_exposure"]
                else:
                    d["reason"] += f" (keyakinan di bawah minimum → pakai Supertrend)"
            except Exception as e:
                d.update(confidence="fallback", provider="fallback:supertrend", provider_label="Cadangan · Supertrend",
                         reason="AI tidak merespons; pakai aturan Supertrend sebagai cadangan.", error=str(e)[:600])
        else:
            d.update(reason="AI dimatikan/tidak ada key; aturan Supertrend.", provider="rule:supertrend",
                     provider_label="Aturan · Supertrend")
    else:
        if w_now == 0:
            target = 0.0; d["reason"] = f"Sinyal {strat}: di luar pasar."
        elif book["position"]:
            target = max(cur, 0.0) if cur > 0.0005 else rule_target; d["reason"] = f"Sinyal {strat}: tetap pegang posisi."
        else:
            d["reason"] = f"Sinyal {strat}: masuk."
            if use_ai:
                try:
                    a, soft = ask_ai(cfg, payload, FILTER_NOTE.format(name=strat, desc=STRATEGY_INFO[strat]))
                    d.update(ai_target=a["target_exposure"], confidence=a["confidence"], reason=a["reason"],
                             provider=a["provider"], provider_label=a["provider_label"], error=" | ".join(soft) or None)
                    if CONF_RANK[a["confidence"]] >= CONF_RANK[cfg["ai"]["min_confidence"]]:
                        target = a["target_exposure"]
                except Exception as e:
                    d.update(confidence="fallback", error=str(e)[:600],
                             reason=f"AI filter gagal; ikut sinyal {strat}.", provider_label=f"Cadangan · {strat}")
    cap = E.caps(R, feat)
    final = max(0.0, min(target, cap))
    blocked = E.entry_blocked(book, R, ts) if final > 0 else None
    if blocked and cur < 0.0005:
        final = 0.0; d["blocked"] = blocked
    d.update(target=round(target, 3), cap=round(cap, 3), final=round(final, 3))
    return d


# ---------- the tick ----------
def tick(force=False):
    cfg, warns, file_ok = load_config()
    s = load_state(cfg)
    ts = now_ts()
    before = json.dumps(s, sort_keys=True)
    s["config_warnings"] = warns
    s["venue"] = cfg["venue"]
    cfg_dirty, errors = False, []
    if cfg["venue"] in ("both", "indodax"):
        try: cfg_dirty = tick_indodax(cfg, s, ts, force, file_ok, before)
        except Exception as e: errors.append("Indodax: " + describe_err(e))
    if cfg["venue"] in ("both", "hyperliquid"):
        req = cfg.get("close_position_request") or ""
        close_req = bool(req) and req != s.get("perps_close_done") and "perps" in s
        if req: s["perps_close_done"] = req
        try:
            llm = (lambda system, payload: ask_json(cfg, system, payload)) if providers(cfg) else None
            PERPS.tick(cfg, s, ts, force, llm, event, iso, get,
                       close_request=close_req, enabled=cfg["enabled"] and not (s.get("kill") or {}).get("active"))
        except Exception as e: errors.append("Hyperliquid: " + describe_err(e))
    finish(s, before, cfg_dirty, cfg)
    if errors: raise RuntimeError(" | ".join(errors))


def tick_indodax(cfg, s, ts, force, file_ok, before):
    """Indodax SOL/IDR spot (unchanged logic). Returns cfg_dirty."""
    cfg_dirty = False

    # user re-enabled the bot after a kill switch -> clear it and restart the loss baseline
    if s.get("kill") and s["kill"].get("active") and cfg["enabled"] and s["kill"].get("disabled_bot"):
        s["kill"]["active"] = False
        for b in s["books"].values(): b["loss_base"] = None
        event(s, "info", "Bot diaktifkan lagi", "Kill switch direset; batas rugi total dihitung dari ekuitas sekarang.")

    mode, why = effective_mode(cfg, file_ok, s)
    if cfg["mode"] == "live" and why:
        event(s, "error", "Live ditolak → PAPER", why, "PAPER", dedupe_hours=12)
    s["mode"] = mode.upper()
    s["requested_mode"] = cfg["mode"].upper()
    book = s["books"][mode]
    execu = live_executor if mode == "live" else None

    if mode == "paper" and not book["position"] and not book["trades"] and book["start_idr"] != cfg["paper"]["start_idr"]:
        st = cfg["paper"]["start_idr"]
        s["books"]["paper"] = book = E.new_book(st)
        s.update(bench={"idr": float(st), "sol": 0.0}, hold={"idr": 0.0, "sol": None}, start_idr=float(st), equity=[])
        event(s, "info", "Modal paper diubah", f"Dompet simulasi direset ke {rp(st)}.")
    if mode == "live" and not book["position"] and not book["trades"] and book["start_idr"] != cfg["live"]["max_capital_idr"]:
        s["books"]["live"] = book = E.new_book(cfg["live"]["max_capital_idr"])

    px = ticker()
    if book.get("loss_base") is None: book["loss_base"] = E.equity(book, px)
    s["price"] = px; s["price_at"] = iso(ts)

    # daily summary + daily loss window (WIB day)
    prev = E.roll_day(book, wib_day(ts), px)
    if prev and prev[0]:
        eqn = E.equity(book, px)
        ch = 100 * (eqn / prev[1] - 1) if prev[1] else 0
        n_tr = sum(1 for t in book["trades"] if wib_day(t["closed_at"]) == prev[0])
        event(s, "summary", f"Ringkasan {prev[0]}",
              f"Ekuitas {rp(eqn)} ({ch:+.2f}% hari itu), {n_tr} trade ditutup, eksposur {E.exposure(book, px)*100:.0f}%, "
              f"SOL {rp(px)}. Bot {'AKTIF' if cfg['enabled'] else 'MATI'}.", mode)

    # manual close request from the app (works even when the bot is OFF)
    req = cfg.get("close_position_request") or ""
    if req and req != s.get("close_request_done"):
        s["close_request_done"] = req
        o, closed = E.close_all(book, px, ts, "tutup manual dari aplikasi", None, execu)
        if o: order_event(s, o, mode, closed)
        else: event(s, "info", "Tidak ada posisi", "Permintaan tutup posisi diterima, tapi tidak ada posisi terbuka.", mode)

    # STOP + sell from the app: liquidate the LIVE book once, even though mode is now paper
    lreq = cfg.get("live_exit_request") or ""
    if lreq and lreq != s.get("live_exit_done"):
        s["live_exit_done"] = lreq
        lb = s["books"]["live"]
        k, sec = indodax_keys()
        if lb["sol"] * px >= E.DUST_IDR and k and sec:
            o, closed = E.close_all(lb, px, ts, "STOP live dari aplikasi", None, live_executor)
            order_event(s, o, "live", closed)
        elif lb["sol"] * px >= E.DUST_IDR:
            event(s, "error", "Gagal jual posisi live", "Secret Indodax tidak tersedia di workflow.", "LIVE")
        else:
            event(s, "info", "LIVE dihentikan", "Tidak ada posisi live untuk dijual.", "LIVE")

    if not cfg["enabled"]:
        s["status"] = "disabled"
        if json.loads(before).get("status") != "disabled":
            event(s, "info", "Bot dimatikan", "Jadwal tetap jalan tapi bot tidak trading. Posisi terbuka dipertahankan.", mode)
        return cfg_dirty
    s["status"] = "active"

    # risk exits on the open position (every tick)
    hit = E.risk_exit(book, cfg["risk"], ts, px)
    if hit:
        reason = {"stoploss": "stop-loss", "trailing_stop": "trailing stop", "roi": "target ROI"}[hit[0]]
        o, closed = E.close_all(book, px, ts, reason, cfg["risk"], execu)
        book["reentry_block"] = True
        if o:
            event(s, "risk", f"{reason.capitalize()} kena · {mode.upper()}",
                  f"Jual {o['sol']:.4f} SOL @ {rp(px)}" + (f", PnL {closed['pnl_pct']:+.2f}%" if closed else ""), mode)

    # loss limits / kill switch
    lc = E.loss_check(book, cfg["risk"], px)
    if lc:
        kind, loss = lc
        o, closed = E.close_all(book, px, ts, f"kill switch ({'harian' if kind == 'daily' else 'total'})", cfg["risk"], execu)
        if o: order_event(s, o, mode, closed)
        if kind == "daily":
            book["daily_block"] = book["day"]
        msg = f"Rugi {loss:.2f}% (batas {'harian' if kind == 'daily' else 'total'}). Posisi ditutup."
        if kind == "total" or mode == "live":
            raw = _raw_config()
            raw["mode"] = "paper"; raw["enabled"] = False
            save_config(raw); cfg_dirty = True
            s["kill"] = {"active": True, "at": iso(ts), "kind": kind, "loss_pct": loss, "mode": mode.upper(), "disabled_bot": True}
            msg += " Bot dimatikan dan mode dikembalikan ke PAPER. Nyalakan lagi dari aplikasi."
            event(s, "kill", "KILL SWITCH", msg, mode)
            s["status"] = "disabled"
            return cfg_dirty
        if not any(e["type"] == "kill" and e["title"].startswith("Batas rugi harian") and wib_day(e["ts"]) == wib_day(ts) for e in s["events"]):
            event(s, "kill", "Batas rugi harian tercapai", msg + " Tidak ada entry baru sampai besok (WIB).", mode)

    # strategy evaluation on a newly closed candle
    tf = cfg["strategy"]["timeframe"]
    m = TF_MINUTES[tf]
    last_closed = (ts // (m * 60)) * m * 60 - m * 60
    key = datetime.fromtimestamp(last_closed, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    if force or s["last_candle"].get(tf) != key:
        evaluate(cfg, s, book, mode, execu, tf, ts, px)
    return cfg_dirty


def _raw_config():
    try:
        raw = json.load(open(CONFIG_PATH))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def evaluate(cfg, s, book, mode, execu, tf, ts, px_now):
    bars = fetch_bars(tf, 320)
    daily = bars if tf == "1d" else fetch_bars("1d", 120)
    if len(bars) < 60: raise RuntimeError(f"candle {tf} terlalu sedikit ({len(bars)})")
    feat = features(bars, daily, tf)
    px = px_now
    d = decide(cfg, s, book, bars, feat, px, ts)
    o, closed = E.rebalance_to(book, d["final"], px, ts, f"{d['strategy']} target {d['final']*100:.0f}%", cfg["risk"],
                               cfg["live"]["max_order_idr"] if mode == "live" else None, execu)
    order_event(s, o, mode, closed)
    act = f"{o['side']} {rp(o['idr'])}" if o else "HOLD"
    # pure Supertrend benchmark + buy & hold, both paper
    st_want = want_series("supertrend", bars)[-1]
    bb = s["bench"]
    bact = _simple_rebalance(bb, float(st_want), px)
    if s["hold"]["sol"] is None: s["hold"]["sol"] = s["start_idr"] * (1 - E.BUY_FEE) / px
    s["last_candle"][tf] = feat["candle_time"]
    entry = {"candle": feat["candle_time"], "date": feat["date"], "tf": tf, "close": px, "strategy": d["strategy"],
             "signal": d["signal"], "ai_target": d["ai_target"], "target": d["target"], "cap": d["cap"], "final": d["final"],
             "confidence": d["confidence"], "reason": d["reason"], "provider": d["provider"],
             "provider_label": d["provider_label"], "action": act, "bench_action": bact, "supertrend": feat["supertrend"],
             "blocked": d.get("blocked"), "error": d["error"], "mode": mode.upper(), "ran_at": iso(ts)}
    s["log"].append(entry); del s["log"][:-MAX_LOG]
    pb = s["books"]["paper"]
    pt = {"t": ts, "date": feat["date"], "close": px, "ai": round(E.equity(pb, px)),
          "bench": round(bb["idr"] + bb["sol"] * px), "hold": round(s["hold"]["sol"] * px)}
    if mode == "live": pt["live"] = round(E.equity(book, px))
    s["equity"].append(pt); del s["equity"][:-MAX_EQUITY_POINTS]
    s["market"] = {k: v for k, v in feat.items() if not k.startswith("_")}
    s["updated_at"] = iso(ts)
    if d["confidence"] == "fallback":
        event(s, "fallback", "AI gagal → aturan cadangan", (d["error"] or "")[:300], mode, dedupe_hours=12)
    elif d["error"]:
        event(s, "error", "Sebagian penyedia AI error", d["error"][:300], mode, dedupe_hours=12)


def _simple_rebalance(w, target, px):
    eq = w["idr"] + w["sol"] * px
    cur = w["sol"] * px / eq if eq else 0
    if abs(target - cur) < E.MIN_STEP: return "HOLD"
    if target > cur:
        spend = (target - cur) * eq; spend = min(spend, w["idr"])
        w["sol"] += spend * (1 - E.BUY_FEE) / px; w["idr"] -= spend
        return f"BUY {rp(spend)}"
    sell = w["sol"] if target == 0 else min((cur - target) * eq / px, w["sol"])
    w["idr"] += sell * px * (1 - E.SELL_FEE); w["sol"] -= sell
    return f"SELL {sell:.4f} SOL"


VOLATILE = ("price", "price_at", "summary", "config_warnings")
PERPS_VOLATILE = ("prices", "prices_at", "summary", "last_check")


def _cmp_view(st):
    v = {k: x for k, x in st.items() if k not in VOLATILE}
    if isinstance(v.get("perps"), dict):
        v["perps"] = {k: x for k, x in v["perps"].items() if k not in PERPS_VOLATILE}
    return json.dumps(v, sort_keys=True)


def finish(s, before, cfg_dirty, cfg):
    s["summary"] = summary(s)
    after = _cmp_view(s)
    prev = json.loads(before)
    prev_cmp = _cmp_view(prev)
    if after != prev_cmp or not os.path.exists(STATE) or prev.get("config_warnings") != s.get("config_warnings"):
        save_state(s); print("state.json diperbarui.")
    else:
        print("Tidak ada perubahan (tick tanpa candle baru / tanpa aksi).")
    if cfg_dirty: print("config/bot-config.json diubah oleh bot (kill switch).")


def summary(s):
    px = s.get("price") or (s["log"][-1]["close"] if s["log"] else 0)
    out = {}
    for name, b in s["books"].items():
        tr = b["trades"]
        wins = [t for t in tr if t["pnl_idr"] > 0]
        out[name] = {"equity": round(E.equity(b, px)), "start": round(b["start_idr"]),
                     "pnl_idr": round(E.equity(b, px) - b["start_idr"]),
                     "exposure": round(E.exposure(b, px), 3), "trades": len(tr),
                     "win_rate": round(100 * len(wins) / len(tr), 1) if tr else None,
                     "realized_idr": round(sum(t["pnl_idr"] for t in tr))}
    return out


def status():
    cfg, warns, _ = load_config()
    s = load_state(cfg); px = ticker(); s["price"] = px
    print(json.dumps({"harga": px, "mode_config": cfg["mode"], "aktif": cfg["enabled"], "ringkasan": summary(s),
                      "peringatan_config": warns, "keputusan_terakhir": s["log"][-1] if s["log"] else None},
                     indent=1, ensure_ascii=False))


def probe():
    cfg, warns, _ = load_config()
    if warns: print("Peringatan config:", warns)
    msgs = [{"role": "system", "content": system_prompt(cfg)}, {"role": "user", "content": json.dumps(
        {"market": {"supertrend": "UP", "rsi14": 55}, "current_exposure": 0, "your_last_decisions": []})}]
    ok = False
    for name, url, key, model, mt in providers(cfg):
        t0 = time.time()
        try:
            d = call_llm(url, key, model, msgs, mt, cfg["ai"]["temperature"])
            print(f"OK  {name}:{model} ({time.time()-t0:.1f}s) -> {d}"); ok = True
        except Exception as e: print(f"ERR {name}:{model} ({time.time()-t0:.1f}s) -> {describe_err(e)}")
    if not providers(cfg): print("Tidak ada penyedia AI aktif yang punya API key.")
    return ok


def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else "tick").lower()
    if arg == "status": return status()
    if arg == "probe": sys.exit(0 if probe() else 1)
    if arg not in ("tick", "run", "paper", "live"): sys.exit("Pakai: aibot.py tick|run|status|probe")
    try:
        tick(force=arg != "tick")
    except Exception as e:
        # record the error for the app, keep the old state otherwise, then fail the job visibly
        cfg, _, _ = load_config()
        try:
            s = load_state(cfg)
            if event(s, "error", "Bot error", describe_err(e), dedupe_hours=6): save_state(s)
        except Exception:
            pass
        print("ERROR:", describe_err(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
