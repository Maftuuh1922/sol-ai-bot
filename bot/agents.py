"""Multi-agent decision layer (inspired by the 7-agent Grok/Hyperliquid design).

Six ANALYST agents each return a structured vote {dir: long|short|flat, conf: 0..1, reason}:
  trend      - EMA 20/50/200 stack + Supertrend direction
  momentum   - RSI 14, MACD histogram, 20-bar rate of change
  regime     - volatility / regime: efficiency ratio (trend vs chop), ATR%, vol spikes
  funding    - funding rate & open interest (crowding, OI confirming price)
  sentiment  - Crypto Fear & Greed index (alternative.me), contrarian at extremes
  liquidity  - L2 order-book depth imbalance + spread, volume surge (backtest: volume only)

Every agent has a deterministic rule version. When an LLM is available all agents for all pairs are
answered in ONE batched call (rule values are passed in as hints); any failure falls back to the rules.

A COORDINATOR aggregates the votes into a weighted score -> long / short / flat.
The RISK AGENT (pure code, never an LLM) has the final veto: it sizes the position from max risk per
trade and an ATR stop, writes stop-loss + take-profit BEFORE entry, enforces max leverage, max
concurrent positions, daily loss limit, kill switch and a liquidation-distance buffer, and rejects
any trade without a stop.
"""
import json, math

from strategies import ema, rsi_series, supertrend

AGENTS = ("trend", "momentum", "regime", "funding", "sentiment", "liquidity")
AGENT_LABEL = {"trend": "Tren", "momentum": "Momentum", "regime": "Volatilitas/Rezim", "funding": "Funding & OI",
               "sentiment": "Sentimen", "liquidity": "Orderbook/Likuiditas"}
WEIGHTS = {"trend": 1.5, "momentum": 1.0, "regime": 0.75, "funding": 0.75, "sentiment": 0.5, "liquidity": 0.5}
DIRS = ("long", "short", "flat")
SIGN = {"long": 1, "short": -1, "flat": 0}


def vote(d, c, reason):
    return {"dir": d, "conf": round(max(0.0, min(1.0, c)), 2), "reason": reason[:160]}


# ---------------- features ----------------
def features(bars, ctx=None, fng=None, book=None, oi_change_pct=None):
    """Compact per-pair snapshot. bars: closed candles (o,h,l,c,v). ctx: market_ctx row (live) or
    {"funding": hourly rate} (backtest). fng: {"value": int, "prev": int}. book: book_stats()."""
    bars = bars[-320:]
    c = [b["c"] for b in bars]
    n = len(c)
    trend, up, dn, atr = supertrend(bars)
    e20, e50, e200 = ema(c, 20)[-1], ema(c, 50)[-1], ema(c, 200)[-1] if n >= 200 else ema(c, n)[-1]
    r = rsi_series(c)
    macd_line = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    sig = ema(macd_line, 9)
    hist = macd_line[-1] - sig[-1]
    roc20 = 100 * (c[-1] / c[-21] - 1) if n > 21 else 0.0
    k = 20
    er = abs(c[-1] - c[-1 - k]) / (sum(abs(c[i] - c[i - 1]) for i in range(n - k, n)) or 1e-9) if n > k else 0.0
    atr_pct = 100 * atr[-1] / c[-1] if atr[-1] else None
    atr_hist = [100 * atr[i] / c[i] for i in range(max(0, n - 100), n) if atr[i]]
    atr_med = sorted(atr_hist)[len(atr_hist) // 2] if atr_hist else atr_pct
    vols = [b.get("v") or 0 for b in bars[-21:-1]]
    vavg = sum(vols) / len(vols) if vols else 0
    vrel = (bars[-1].get("v") or 0) / vavg if vavg else None
    f = {
        "close": c[-1], "ema20": e20, "ema50": e50, "ema200": e200,
        "supertrend": "UP" if trend[-1] == 1 else "DOWN", "rsi14": round(r[-1] if r[-1] is not None else 50, 1),
        "macd_hist_pct": round(100 * hist / c[-1], 3), "roc20_pct": round(roc20, 2), "efficiency_ratio": round(er, 2),
        "atr": atr[-1], "atr_pct": round(atr_pct, 2) if atr_pct else None, "atr_pct_median100": round(atr_med, 2) if atr_med else None,
        "last_bar_ret_pct": round(100 * (c[-1] / c[-2] - 1), 2) if n > 1 else 0.0,
        "volume_rel20": round(vrel, 2) if vrel is not None else None,
    }
    ctx = ctx or {}
    if ctx.get("funding") is not None:
        f["funding_hourly_pct"] = round(100 * ctx["funding"], 5)
        f["funding_apr_pct"] = round(100 * ctx["funding"] * 24 * 365, 1)
    if ctx.get("premium") is not None: f["premium_pct"] = round(100 * ctx["premium"], 4)
    if oi_change_pct is not None: f["oi_change_24h_pct"] = round(oi_change_pct, 2)
    if ctx.get("prev_day_px"): f["ret_24h_pct"] = round(100 * (ctx.get("mark", c[-1]) / ctx["prev_day_px"] - 1), 2)
    if fng: f["fear_greed"] = fng.get("value"); f["fear_greed_prev"] = fng.get("prev")
    if book: f.update(book_imbalance=book["imbalance"], spread_bps=book["spread_bps"], depth_usd_1pct=book.get("depth_usd_1pct"))
    return f


# ---------------- rule agents ----------------
def a_trend(f):
    c, e20, e50, e200, st = f["close"], f["ema20"], f["ema50"], f["ema200"], f["supertrend"]
    if c > e50 > e200 and st == "UP": return vote("long", 0.85 if e20 > e50 else 0.65, "Harga > EMA50 > EMA200, Supertrend naik")
    if c < e50 < e200 and st == "DOWN": return vote("short", 0.85 if e20 < e50 else 0.65, "Harga < EMA50 < EMA200, Supertrend turun")
    if st == "UP" and c > e200: return vote("long", 0.4, "Supertrend naik di atas EMA200, struktur belum rapi")
    if st == "DOWN" and c < e200: return vote("short", 0.4, "Supertrend turun di bawah EMA200, struktur belum rapi")
    return vote("flat", 0.5, "Tren campur (Supertrend vs EMA200 berlawanan)")


def a_momentum(f):
    r, h, roc = f["rsi14"], f["macd_hist_pct"], f["roc20_pct"]
    if r >= 78: return vote("flat", 0.6, f"RSI {r} jenuh beli, jangan kejar")
    if r <= 22: return vote("flat", 0.6, f"RSI {r} jenuh jual, jangan kejar short")
    if r > 52 and h > 0 and roc > 0: return vote("long", min(0.9, 0.45 + abs(roc) / 40 + (r - 52) / 60), f"RSI {r}, MACD positif, ROC20 {roc:+.1f}%")
    if r < 48 and h < 0 and roc < 0: return vote("short", min(0.9, 0.45 + abs(roc) / 40 + (48 - r) / 60), f"RSI {r}, MACD negatif, ROC20 {roc:+.1f}%")
    return vote("flat", 0.4, f"Momentum lemah/campur (RSI {r}, ROC20 {roc:+.1f}%)")


def a_regime(f):
    er, ap, med = f["efficiency_ratio"], f["atr_pct"], f["atr_pct_median100"]
    d = "long" if f["roc20_pct"] > 0 else "short"
    if ap and med and ap > 2.2 * med: return vote("flat", 0.8, f"Volatilitas ekstrem (ATR {ap}% vs median {med}%)")
    if er >= 0.35: return vote(d, min(0.85, 0.4 + er), f"Rezim trending (ER {er}), ikut arah 20 bar")
    if er < 0.2: return vote("flat", 0.7, f"Rezim sideways/chop (ER {er})")
    return vote(d, 0.25, f"Rezim transisi (ER {er})")


def a_funding(f):
    fh = f.get("funding_hourly_pct")
    oi = f.get("oi_change_24h_pct"); r24 = f.get("ret_24h_pct", f.get("last_bar_ret_pct", 0))
    if fh is None: return vote("flat", 0.0, "Tidak ada data funding")
    apr = f.get("funding_apr_pct", fh * 24 * 365)
    if apr >= 40: return vote("short", 0.5, f"Funding sangat positif ({apr:.0f}%/thn): long terlalu ramai")
    if apr <= -30: return vote("long", 0.5, f"Funding sangat negatif ({apr:.0f}%/thn): short terlalu ramai")
    if oi is not None and abs(oi) >= 3:
        if oi > 0 and r24 > 0: return vote("long", 0.5, f"OI naik {oi:+.1f}% bersama harga: uang baru masuk long")
        if oi > 0 and r24 < 0: return vote("short", 0.5, f"OI naik {oi:+.1f}% saat harga turun: short baru")
        if oi < 0: return vote("flat", 0.4, f"OI turun {oi:+.1f}%: posisi ditutup, tren melemah")
    if apr > 15: return vote("long", 0.2, f"Funding positif moderat ({apr:.0f}%/thn)")
    if apr < -5: return vote("short", 0.2, f"Funding negatif ({apr:.0f}%/thn)")
    return vote("flat", 0.3, f"Funding netral ({apr:.0f}%/thn)")


def a_sentiment(f):
    v, p = f.get("fear_greed"), f.get("fear_greed_prev")
    if v is None: return vote("flat", 0.0, "Fear & Greed tidak tersedia")
    if v <= 15: return vote("long", 0.55, f"Extreme fear ({v}): kontrarian beli")
    if v >= 85: return vote("short", 0.45, f"Extreme greed ({v}): kontrarian waspada")
    if v <= 25: return vote("long", 0.3, f"Fear ({v})")
    if v >= 75: return vote("short", 0.25, f"Greed tinggi ({v})")
    if p is not None and abs(v - p) >= 8: return vote("long" if v > p else "short", 0.25, f"Sentimen bergeser {p}→{v}")
    return vote("flat", 0.3, f"Sentimen netral ({v})")


def a_liquidity(f):
    imb, sp, vr = f.get("book_imbalance"), f.get("spread_bps"), f.get("volume_rel20")
    if sp is not None and sp > 15: return vote("flat", 0.6, f"Spread lebar {sp} bps: likuiditas tipis")
    if imb is not None and abs(imb) >= 0.25:
        return vote("long" if imb > 0 else "short", min(0.6, abs(imb)), f"Kedalaman ±1% condong {'bid' if imb > 0 else 'ask'} ({imb:+.2f})")
    if vr is not None and vr >= 1.8 and abs(f["last_bar_ret_pct"]) > 0.3:
        return vote("long" if f["last_bar_ret_pct"] > 0 else "short", 0.35, f"Volume {vr}x rata-rata searah candle terakhir")
    if imb is None and vr is None: return vote("flat", 0.0, "Tidak ada data orderbook/volume")
    return vote("flat", 0.3, "Likuiditas seimbang")


RULES = {"trend": a_trend, "momentum": a_momentum, "regime": a_regime, "funding": a_funding,
         "sentiment": a_sentiment, "liquidity": a_liquidity}


def rule_votes(f):
    return {k: dict(RULES[k](f), source="rule") for k in AGENTS}


# ---------------- batched LLM ----------------
LLM_SYSTEM = """You run SIX analyst agents for a crypto perpetuals PAPER trading bot on Hyperliquid.
Agents: trend (EMA stack, Supertrend), momentum (RSI, MACD, ROC), regime (volatility / trend-vs-chop),
funding (funding rate, open interest, crowding), sentiment (Fear & Greed), liquidity (order book, spread, volume).
For EACH coin and EACH agent, judge only that agent's evidence in the features and vote.
Each coin includes "rule_votes" from deterministic versions of the agents; you may agree or disagree, with reason.
You do NOT size positions or set stops: a separate risk agent (code) does that and can veto anything.
Prefer "flat" when evidence is weak. Confidence 0.0-1.0.
Reply with ONLY JSON, no prose:
{"coins":{"<COIN>":{"trend":{"dir":"long|short|flat","conf":0.0,"reason":"<=12 words, Bahasa Indonesia"},"momentum":{...},"regime":{...},"funding":{...},"sentiment":{...},"liquidity":{...}}}}"""


def llm_payload(feats, rules):
    def slim(f):
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in f.items() if k not in ("atr",)}
    return {"coins": {c: {"features": slim(feats[c]), "rule_votes": {a: [v["dir"], v["conf"]] for a, v in rules[c].items()}}
                      for c in feats}}


def parse_llm_votes(d, coins):
    """Validate the LLM JSON. Returns {coin: {agent: vote}} for whatever is valid; missing -> absent."""
    out = {}
    src = d.get("coins") if isinstance(d, dict) else None
    if not isinstance(src, dict): raise ValueError("JSON tanpa 'coins'")
    for coin in coins:
        cv = src.get(coin) or src.get(coin.upper()) or src.get(coin.lower())
        if not isinstance(cv, dict): continue
        for a in AGENTS:
            v = cv.get(a)
            if not isinstance(v, dict): continue
            dr = str(v.get("dir", "")).lower()
            if dr not in DIRS: continue
            try: cf = float(v.get("conf", 0.5))
            except Exception: cf = 0.5
            if cf > 1: cf = cf / 100 if cf <= 100 else 1.0
            out.setdefault(coin, {})[a] = vote(dr, cf, str(v.get("reason", "")))
    if not out: raise ValueError("tidak ada vote valid dari AI")
    return out


def analyst_votes(feats, ask_json=None):
    """Votes for every coin. ask_json(system, payload, max_tokens) -> (dict, label) or raises.
    Returns (votes, source_label, error)."""
    rules = {c: rule_votes(f) for c, f in feats.items()}
    if not ask_json: return rules, "Aturan (tanpa AI)", None
    try:
        d, label = ask_json(LLM_SYSTEM, llm_payload(feats, rules))
        got = parse_llm_votes(d, list(feats))
        votes, missing = {}, 0
        for c in feats:
            votes[c] = {}
            for a in AGENTS:
                if a in got.get(c, {}): votes[c][a] = dict(got[c][a], source="llm")
                else: votes[c][a] = rules[c][a]; missing += 1
        return votes, label, (f"{missing} vote AI tidak valid → aturan" if missing else None)
    except Exception as e:
        return rules, "Cadangan · aturan", str(e)[:400]


# ---------------- coordinator ----------------
def coordinate(votes, threshold=0.25, allow_short=True):
    num = den = 0.0
    for a, v in votes.items():
        w = WEIGHTS.get(a, 0.5)
        num += w * SIGN[v["dir"]] * v["conf"]
        den += w
    score = num / den if den else 0.0
    longs = sum(1 for v in votes.values() if v["dir"] == "long")
    shorts = sum(1 for v in votes.values() if v["dir"] == "short")
    if score >= threshold: d = "long"
    elif score <= -threshold and allow_short: d = "short"
    else: d = "flat"
    why = f"Skor {score:+.2f} (ambang ±{threshold}); {longs} long, {shorts} short, {len(votes) - longs - shorts} flat"
    if score <= -threshold and not allow_short: why += "; short dimatikan"
    return {"direction": d, "score": round(score, 3), "confidence": round(min(1.0, abs(score) / max(threshold, 1e-9) * 0.5), 2),
            "longs": longs, "shorts": shorts, "reason": why}


# ---------------- risk agent (deterministic, final veto) ----------------
def risk_agent(coin, coord, f, R, book_state, exch_max_lev=50):
    """Decide whether a new position may open and with what parameters.

    book_state: {equity, free_margin, open_positions (int), has_position (bool), daily_loss_pct,
                 total_loss_pct, kill_active, daily_block}
    Returns {"approved": bool, "veto": reason|None, "plan": {...}|None, "checks": [...]}.
    """
    checks = []
    def veto(msg):
        return {"approved": False, "veto": msg, "plan": None, "checks": checks}
    d = coord["direction"]
    if d == "flat": return veto("Koordinator: flat (tidak ada sinyal cukup kuat)")
    if book_state.get("kill_active"): return veto("Kill switch aktif")
    if book_state.get("daily_block") or (R["daily_loss_enabled"] and book_state["daily_loss_pct"] >= R["daily_max_loss_pct"]):
        return veto(f"Batas rugi harian {R['daily_max_loss_pct']}% tercapai")
    if R["kill_switch_enabled"] and book_state["total_loss_pct"] >= R["total_max_loss_pct"]:
        return veto(f"Rugi total ≥ {R['total_max_loss_pct']}% (kill switch)")
    if book_state.get("has_position"): return veto("Sudah ada posisi di pair ini")
    if book_state["open_positions"] >= R["max_positions"]: return veto(f"Maks {R['max_positions']} posisi bersamaan")
    checks.append("loss limits ok")
    px, atr = f["close"], f.get("atr")
    if not atr or atr <= 0 or not px: return veto("Tidak bisa menghitung stop (ATR kosong) → trade tanpa stop ditolak")
    stop_dist = R["stop_atr_mult"] * atr
    stop_pct = stop_dist / px
    if stop_pct < 0.002: return veto(f"Stop terlalu dekat ({100*stop_pct:.2f}%)")
    if stop_pct > 0.25: return veto(f"Stop terlalu jauh ({100*stop_pct:.1f}%)")
    s = 1 if d == "long" else -1
    stop = px - s * stop_dist
    tp = px + s * R["take_profit_r"] * stop_dist if R["take_profit_r"] > 0 else None
    eq = book_state["equity"]
    risk_usd = eq * R["risk_per_trade_pct"] / 100
    # loss at stop ~= notional * (stop_pct + round-trip costs)
    cost = 2 * (0.00045 + 0.0002)
    notional = risk_usd / (stop_pct + cost)
    max_lev = max(1, min(int(R["max_leverage"]), 5, int(exch_max_lev or 5)))
    mmr = 1.0 / (2.0 * max(1, exch_max_lev or 5))
    # lowest leverage that fits the margin; then make sure liquidation sits far beyond the stop
    free = max(0.0, book_state["free_margin"])
    if free <= 1: return veto("Margin bebas habis")
    lev = max(1, math.ceil(notional / free))
    if lev > max_lev:
        notional = free * max_lev; lev = max_lev
        checks.append(f"ukuran dipangkas ke margin bebas × {max_lev}x")
    def liq_of(L):
        return px * (1 - 1 / L) / (1 - mmr) if s == 1 else px * (1 + 1 / L) / (1 + mmr)
    liq = liq_of(lev)
    need = R["liq_buffer_mult"] * stop_dist
    while abs(px - liq) < need and lev > 1:
        lev -= 1
        if notional / lev > free: notional = free * lev
        liq = liq_of(lev)
    if abs(px - liq) < need and lev == 1:
        # 1x long cannot liquidate (liq <= 0); 1x short liq is ~2x price
        if not (s == 1 and liq <= 0) and abs(px - liq) < need:
            return veto("Jarak likuidasi terlalu dekat dengan stop")
    if notional < 10: return veto(f"Ukuran ${notional:.2f} di bawah minimum $10 (modal/risk terlalu kecil)")
    risk_real = notional * (stop_pct + cost)
    checks += [f"risk {R['risk_per_trade_pct']}% = ${risk_usd:.2f}", f"stop {R['stop_atr_mult']}×ATR = {100*stop_pct:.2f}%",
               f"leverage {lev}x (maks {max_lev}x)", f"likuidasi {abs(px-liq)/px*100:.1f}% dari harga (≥ {R['liq_buffer_mult']}× stop)"]
    plan = {"side": d, "entry_ref": px, "stop": stop, "tp": tp, "liq": max(liq, 0.0), "leverage": lev,
            "notional": round(notional, 2), "margin": round(notional / lev, 2), "risk_usd": round(risk_real, 2),
            "stop_pct": round(100 * stop_pct, 2)}
    return {"approved": True, "veto": None, "plan": plan, "checks": checks}
