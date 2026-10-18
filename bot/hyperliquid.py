"""Hyperliquid perps: PUBLIC market data + a PAPER-only perpetuals engine.

Market data comes from the public info endpoint (https://api.hyperliquid.xyz/info, no API key):
candles, mids, funding, open interest, L2 book, funding history.

The paper engine simulates isolated-margin perps: taker fee, slippage, hourly funding, leverage,
liquidation price, stop-loss / take-profit written BEFORE entry. There is NO live order path:
`live_order` is a disabled stub that always raises. Nothing in this repo signs Hyperliquid actions.
"""
import json, math, time, urllib.error, urllib.request

INFO_URL = "https://api.hyperliquid.xyz/info"
TAKER_FEE = 0.00045        # Hyperliquid base tier taker fee (0.045%)
SLIPPAGE = 0.0002          # 0.02% assumed slippage per fill
MIN_NOTIONAL = 10.0        # Hyperliquid minimum order value (USD)
HARD_MAX_LEVERAGE = 5      # absolute cap of this bot, whatever the config says
MAX_LIST = 300
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


# ---------------- public info API ----------------
def info(payload, timeout=30, retries=4):
    """POST to the public info endpoint; retries on 429 / 5xx / network errors with backoff."""
    for i in range(retries + 1):
        req = urllib.request.Request(INFO_URL, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "solbot/3.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if i == retries or not (e.code == 429 or e.code >= 500): raise
        except (urllib.error.URLError, TimeoutError):
            if i == retries: raise
        time.sleep(min(20, 2 * 2 ** i))


def candles(coin, tf, start_ms, end_ms=None):
    """Closed + open candles (o/h/l/c/v floats, t in seconds). The API returns at most 5000 per call."""
    end_ms = end_ms or int(time.time() * 1000)
    out, cur = {}, start_ms
    while cur < end_ms:
        rows = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": tf, "startTime": cur, "endTime": end_ms}})
        if not rows: break
        for r in rows:
            out[r["t"]] = dict(t=r["t"] // 1000, o=float(r["o"]), h=float(r["h"]), l=float(r["l"]), c=float(r["c"]),
                               v=float(r.get("v") or 0))
        last = rows[-1]["t"]
        if len(rows) < 4900 or last <= cur: break
        cur = last + 1
    return [out[k] for k in sorted(out)]


def closed_candles(coin, tf, n_bars):
    now = int(time.time() * 1000)
    step = TF_MS[tf]
    bars = candles(coin, tf, now - (n_bars + 2) * step, now)
    return [b for b in bars if b["t"] * 1000 + step <= now]


def market_ctx():
    """{coin: {mark, mid, funding (hourly rate), oi (coins), oi_usd, max_leverage, prev_day_px, day_volume_usd}}"""
    meta, ctxs = info({"type": "metaAndAssetCtxs"})
    out = {}
    for a, c in zip(meta["universe"], ctxs):
        if a.get("isDelisted"): continue
        mark = float(c.get("markPx") or 0)
        out[a["name"]] = {"mark": mark, "mid": float(c.get("midPx") or mark or 0), "funding": float(c.get("funding") or 0),
                          "oi": float(c.get("openInterest") or 0), "oi_usd": float(c.get("openInterest") or 0) * mark,
                          "premium": float(c.get("premium") or 0), "max_leverage": int(a.get("maxLeverage") or 3),
                          "prev_day_px": float(c.get("prevDayPx") or 0), "day_volume_usd": float(c.get("dayNtlVlm") or 0)}
    return out


def l2_book(coin):
    d = info({"type": "l2Book", "coin": coin})
    bids = [(float(x["px"]), float(x["sz"])) for x in d["levels"][0]]
    asks = [(float(x["px"]), float(x["sz"])) for x in d["levels"][1]]
    return bids, asks


def book_stats(bids, asks, band=0.01):
    """Depth imbalance within +-band of mid (-1..1) and spread in bps."""
    if not bids or not asks: return None
    mid = (bids[0][0] + asks[0][0]) / 2
    bd = sum(p * s for p, s in bids if p >= mid * (1 - band))
    ad = sum(p * s for p, s in asks if p <= mid * (1 + band))
    tot = bd + ad
    return {"imbalance": round((bd - ad) / tot, 3) if tot else 0.0, "spread_bps": round(1e4 * (asks[0][0] - bids[0][0]) / mid, 2),
            "depth_usd_1pct": round(tot)}


def funding_history(coin, start_ms, end_ms=None, pause=1.5):
    """[(t_seconds, hourly_rate)] — paged, 500 rows per call."""
    end_ms = end_ms or int(time.time() * 1000)
    out, cur = {}, start_ms
    for _ in range(60):
        rows = info({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": end_ms})
        if not rows: break
        for r in rows: out[r["time"] // 1000] = float(r["fundingRate"])
        last = rows[-1]["time"]
        if len(rows) < 500 or last <= cur: break
        cur = last + 1
        time.sleep(pause)
    return sorted(out.items())


def live_order(*_a, **_k):
    """DISABLED STUB. Live Hyperliquid trading is intentionally not implemented in this bot.
    TODO (only if the owner ever decides to): would need an agent-wallet key, EIP-712 signing and a
    separate review. Until then every call fails loudly so no code path can place a real order."""
    raise RuntimeError("Hyperliquid LIVE tidak tersedia: bot ini hanya PAPER untuk Hyperliquid.")


# ---------------- paper perps engine ----------------
def new_book(start_usd):
    return {"start_usd": float(start_usd), "balance": float(start_usd), "positions": {}, "trades": [], "orders": [],
            "fees_paid": 0.0, "funding_paid": 0.0, "seq": 0, "day": None, "day_start_eq": float(start_usd),
            "daily_block": None, "loss_base": float(start_usd), "kill": None, "last_funding_hour": None}


def _id(b, prefix, ts):
    b["seq"] = b.get("seq", 0) + 1
    return f"{prefix}{int(ts)}-{b['seq']}"


def _push(lst, x):
    lst.append(x); del lst[:-MAX_LIST]


def side_sign(side): return 1 if side == "long" else -1


def maint_rate(max_lev):
    """Hyperliquid maintenance margin = half of the initial margin at max leverage."""
    return 1.0 / (2.0 * max(1, max_lev))


def liq_price(side, entry, lev, mmr):
    """Isolated margin liquidation price (fees ignored)."""
    if side == "long": return entry * (1 - 1 / lev) / (1 - mmr)
    return entry * (1 + 1 / lev) / (1 + mmr)


def unrealized(p, px): return side_sign(p["side"]) * p["size"] * (px - p["entry"])


def equity(b, prices):
    return b["balance"] + sum(unrealized(p, prices.get(c, p["entry"])) for c, p in b["positions"].items())


def used_margin(b): return sum(p["margin"] for p in b["positions"].values())


def free_margin(b, prices):
    return equity(b, prices) - used_margin(b)


def open_position(b, coin, plan, px, ts, reason):
    """Open a paper position from a risk-agent plan. Refuses any plan without a valid stop."""
    side, stop, tp = plan["side"], plan.get("stop"), plan.get("tp")
    if side not in ("long", "short"): raise ValueError("side harus long/short")
    if not stop or stop <= 0: raise ValueError("ditolak: trade tanpa stop-loss")
    if (side == "long" and stop >= px) or (side == "short" and stop <= px): raise ValueError("ditolak: stop di sisi yang salah")
    if coin in b["positions"]: raise ValueError(f"sudah ada posisi {coin}")
    lev = min(plan["leverage"], HARD_MAX_LEVERAGE)
    fill = px * (1 + side_sign(side) * SLIPPAGE)
    size = plan["notional"] / px
    notional = size * fill
    fee = notional * TAKER_FEE
    margin = notional / lev
    b["balance"] -= fee; b["fees_paid"] += fee
    p = {"id": _id(b, "P", ts), "coin": coin, "side": side, "size": size, "entry": fill, "leverage": lev,
         "margin": margin, "notional": notional, "stop": stop, "tp": tp, "liq": plan["liq"], "init_stop": stop,
         "risk_usd": plan["risk_usd"], "r_dist": abs(fill - stop), "opened_at": ts, "fees": fee, "funding": 0.0,
         "be_moved": False, "reason": reason}
    b["positions"][coin] = p
    _push(b["orders"], {"id": _id(b, "O", ts), "ts": ts, "coin": coin, "side": "BUY" if side == "long" else "SELL",
                        "action": "open", "size": round(size, 6), "price": fill, "notional": round(notional, 2),
                        "fee": round(fee, 4), "reason": reason})
    return p


def close_position(b, coin, px, ts, reason, liquidated=False):
    p = b["positions"].pop(coin, None)
    if not p: return None
    s = side_sign(p["side"])
    if liquidated:
        fill = px; fee = 0.0
        pnl = -p["margin"]                     # isolated margin lost
    else:
        fill = px * (1 - s * SLIPPAGE)
        fee = p["size"] * fill * TAKER_FEE
        pnl = s * p["size"] * (fill - p["entry"]) - fee
        pnl = max(pnl, -p["margin"])           # isolated: cannot lose more than the margin
    b["balance"] += pnl; b["fees_paid"] += fee
    total = pnl - p["fees"] - p["funding"]     # entry fee + funding were already booked into balance
    t = {"id": p["id"], "coin": coin, "side": p["side"], "opened_at": p["opened_at"], "closed_at": ts,
         "entry": p["entry"], "exit": fill, "size": round(p["size"], 6), "leverage": p["leverage"],
         "stop": p["init_stop"], "tp": p["tp"], "liq": p["liq"], "notional": round(p["notional"], 2),
         "pnl_usd": round(total, 4), "pnl_pct_margin": round(100 * total / p["margin"], 2) if p["margin"] else 0.0,
         "r_multiple": round(total / p["risk_usd"], 2) if p["risk_usd"] else None,
         "fees_usd": round(p["fees"] + fee, 4), "funding_usd": round(p["funding"], 4), "exit_reason": reason}
    _push(b["trades"], t)
    _push(b["orders"], {"id": _id(b, "O", ts), "ts": ts, "coin": coin, "side": "SELL" if p["side"] == "long" else "BUY",
                        "action": "liquidation" if liquidated else "close", "size": round(p["size"], 6), "price": fill,
                        "notional": round(p["size"] * fill, 2), "fee": round(fee, 4), "reason": reason})
    return t


def apply_funding(b, coin, rate, px, hours=1.0):
    """Hourly funding: positive rate -> longs pay shorts."""
    p = b["positions"].get(coin)
    if not p or not rate: return 0.0
    pay = side_sign(p["side"]) * p["size"] * px * rate * hours
    b["balance"] -= pay; b["funding_paid"] += pay; p["funding"] += pay
    return pay


def check_exit(p, bar_l, bar_h, bar_o=None):
    """Exit for one candle (worst case first: liquidation, then stop, then take-profit).
    Returns (reason, price, liquidated) or None. Gap through a level fills at the open."""
    s = side_sign(p["side"])
    o = bar_o
    if s == 1:
        if bar_l <= p["liq"] and p["liq"] >= p["stop"]: return "liquidation", p["liq"], True
        if bar_l <= p["stop"]: return ("stoploss" if not p["be_moved"] else "breakeven_stop"), (min(o, p["stop"]) if o else p["stop"]), False
        if p["tp"] and bar_h >= p["tp"]: return "take_profit", (max(o, p["tp"]) if o else p["tp"]), False
    else:
        if bar_h >= p["liq"] and p["liq"] <= p["stop"]: return "liquidation", p["liq"], True
        if bar_h >= p["stop"]: return ("stoploss" if not p["be_moved"] else "breakeven_stop"), (max(o, p["stop"]) if o else p["stop"]), False
        if p["tp"] and bar_l <= p["tp"]: return "take_profit", (min(o, p["tp"]) if o else p["tp"]), False
    return None


def maybe_breakeven(p, bar_l, bar_h, at_r):
    """Move the stop to entry (+fees) once price has gone at_r * R in our favour."""
    if not at_r or p["be_moved"] or not p["r_dist"]: return False
    s = side_sign(p["side"])
    trigger = p["entry"] + s * at_r * p["r_dist"]
    if (s == 1 and bar_h >= trigger) or (s == -1 and bar_l <= trigger):
        p["stop"] = p["entry"] * (1 + s * 2 * (TAKER_FEE + SLIPPAGE))
        p["be_moved"] = True
        return True
    return False


def roll_day(b, day, prices):
    if b.get("day") == day: return None
    prev = (b.get("day"), b.get("day_start_eq"))
    b["day"], b["day_start_eq"], b["daily_block"] = day, equity(b, prices), None
    return prev


def loss_state(b, prices):
    eq = equity(b, prices)
    daily = 100 * (1 - eq / b["day_start_eq"]) if b.get("day_start_eq") else 0.0
    total = 100 * (1 - eq / b["loss_base"]) if b.get("loss_base") else 0.0
    return round(daily, 2), round(total, 2)
