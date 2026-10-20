"""Indicators and strategies.

Each strategy turns a list of closed candles into a per-candle "want" series (1 = hold SOL, 0 = stay in IDR)
the same way Freqtrade strategies populate entry/exit columns. Rules are re-implemented from public,
well-known ideas (no Freqtrade code is copied; see README for credits).
"""
import math

STRATEGY_INFO = {
    "ai_supertrend": "AI + Supertrend (10,3): AI memilih eksposur, Supertrend jadi aturan cadangan",
    "ema_rsi": "EMA 12/26 cross + RSI: masuk saat EMA12 memotong ke atas EMA26 dan RSI<70; keluar saat potong ke bawah atau RSI>80",
    "bollinger": "Bollinger (20,2) mean-reversion + RSI: masuk saat close < band bawah dan RSI<30; keluar saat RSI>70",
}
WARMUP = 210   # bars needed before a strategy is trusted (EMA200 for features)


def ema(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n: s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def stdev(xs, n):
    out = []
    for i in range(len(xs)):
        if i < n - 1: out.append(None); continue
        w = xs[i - n + 1:i + 1]; m = sum(w) / n
        out.append(math.sqrt(sum((x - m) ** 2 for x in w) / n))
    return out


def rsi_series(xs, n=14):
    out = [None] * len(xs)
    if len(xs) <= n: return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    out[n] = 100 - 100 / (1 + g / l) if l else 100.0
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + g / l) if l else 100.0
    return out


def rsi(xs, n=14):
    s = rsi_series(xs, n)
    return s[-1] if s and s[-1] is not None else 50.0


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
        if i and up[i - 1] is not None and bars[i - 1]["c"] > up[i - 1]: bu = max(bu, up[i - 1])
        if i and dn[i - 1] is not None and bars[i - 1]["c"] < dn[i - 1]: bd = min(bd, dn[i - 1])
        up[i], dn[i] = bu, bd
        t = trend[i - 1] if i else 1
        if t == -1 and i and dn[i - 1] is not None and bars[i]["c"] > dn[i - 1]: t = 1
        elif t == 1 and i and up[i - 1] is not None and bars[i]["c"] < up[i - 1]: t = -1
        trend[i] = t
    return trend, up, dn, atr


def supertrend_line(bars):
    trend, up, dn, _ = supertrend(bars)
    return [(up[i] if trend[i] == 1 else dn[i]) for i in range(len(bars))], trend


def want_series(name, bars):
    """Per-bar desired state (1 in SOL / 0 in IDR) for a rule-based strategy."""
    c = [b["c"] for b in bars]
    n = len(c)
    if name in ("ai_supertrend", "supertrend"):
        trend = supertrend(bars)[0]
        return [1 if t == 1 else 0 for t in trend]
    r = rsi_series(c)
    want, cur = [0] * n, 0
    if name == "ema_rsi":
        f, s = ema(c, 12), ema(c, 26)
        for i in range(1, n):
            cu = f[i - 1] <= s[i - 1] and f[i] > s[i]
            cd = f[i - 1] >= s[i - 1] and f[i] < s[i]
            ri = r[i] if r[i] is not None else 50
            if cur == 0 and cu and ri < 70: cur = 1
            elif cur == 1 and (cd or ri > 80): cur = 0
            want[i] = cur
        return want
    if name == "bollinger":
        mid, sd = sma(c, 20), stdev(c, 20)
        for i in range(n):
            if mid[i] is None or r[i] is None: want[i] = cur; continue
            lower = mid[i] - 2 * sd[i]
            if cur == 0 and c[i] < lower and r[i] < 30: cur = 1
            elif cur == 1 and r[i] > 70: cur = 0
            want[i] = cur
        return want
    raise ValueError(f"strategi tidak dikenal: {name}")


def trend_filter(bars, daily, ma, tf_sec):
    """Per bar of `bars`: True when the latest daily candle CLOSED by that bar's close is above its SMA(ma).
    Uses only daily candles already closed at that moment (no lookahead). ma <= 0 -> always True.
    Not enough daily history for the SMA -> False (no new entry without a known trend)."""
    if not ma or ma <= 0: return [True] * len(bars)
    dc = [d["c"] for d in daily]
    s = sma(dc, int(ma))
    out, j = [], -1
    for b in bars:
        close_ts = b["t"] + tf_sec
        while j + 1 < len(daily) and daily[j + 1]["t"] + 86400 <= close_ts: j += 1
        out.append(j >= 0 and s[j] is not None and dc[j] > s[j])
    return out


def trend_state(daily, ma):
    """Latest closed daily candle vs SMA(ma) for the live bot: (ok, close, sma)."""
    if not ma or ma <= 0: return True, None, None
    dc = [d["c"] for d in daily]
    if len(dc) < ma: return False, (dc[-1] if dc else None), None
    v = sum(dc[-int(ma):]) / int(ma)
    return dc[-1] > v, dc[-1], v


TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
TF_API = {"15m": "15", "1h": "60", "4h": "240", "1d": "1D"}


def features(bars, daily=None, tf="1d"):
    """Market snapshot for the AI and the dashboard. Volatility is always measured on daily candles."""
    c = [b["c"] for b in bars]
    trend, up, dn, atr = supertrend(bars)
    d = daily or bars
    dc = [b["c"] for b in d]
    rets = [math.log(dc[i] / dc[i - 1]) for i in range(max(1, len(dc) - 30), len(dc))]
    mu = sum(rets) / max(len(rets), 1)
    vol = math.sqrt(sum((x - mu) ** 2 for x in rets) / max(len(rets) - 1, 1)) * math.sqrt(365) if len(rets) > 2 else 0.6
    datr = supertrend(d)[3][-1] if len(d) > 11 else None
    e50, e200 = ema(c, 50)[-1], ema(c, 200)[-1]
    flips = [i for i in range(max(1, len(trend) - 60), len(trend)) if trend[i] != trend[i - 1]]
    per = lambda k: round(100 * (c[-1] / c[-1 - k] - 1), 2) if len(c) > k else None
    from datetime import datetime, timezone
    return {
        "timeframe": tf,
        "candle_time": datetime.fromtimestamp(bars[-1]["t"], timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "date": datetime.fromtimestamp(bars[-1]["t"], timezone.utc).strftime("%Y-%m-%d"),
        "close_idr": c[-1],
        "supertrend": "UP" if trend[-1] == 1 else "DOWN",
        "supertrend_line_idr": round(up[-1] if trend[-1] == 1 else dn[-1]) if (up[-1] or dn[-1]) else None,
        "bars_since_trend_flip": (len(trend) - 1 - flips[-1]) if flips else ">60",
        "ema50_idr": round(e50), "ema200_idr": round(e200),
        "rsi14": round(rsi(c), 1),
        "atr14_pct": round(100 * atr[-1] / c[-1], 2) if atr[-1] else None,
        "daily_atr14_pct": round(100 * datr / dc[-1], 2) if datr else None,
        "realized_vol_30d_annual_pct": round(100 * vol, 1),
        "ret_7d_pct": round(100 * (dc[-1] / dc[-8] - 1), 2) if len(dc) > 8 else None,
        "ret_30d_pct": round(100 * (dc[-1] / dc[-31] - 1), 2) if len(dc) > 31 else None,
        "ret_90d_pct": round(100 * (dc[-1] / dc[-91] - 1), 2) if len(dc) > 91 else None,
        "ret_10bars_pct": per(10),
        "drawdown_from_365d_high_pct": round(100 * (dc[-1] / max(dc[-365:]) - 1), 2),
        "last_10_closes_idr": [round(x) for x in c[-10:]],
        "_vol": vol, "_trend": trend[-1],
        "_datr_pct": (100 * datr / dc[-1]) if datr else None,
    }
