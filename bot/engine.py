"""Wallet / position / risk engine shared by the live bot and the backtester.

Freqtrade-style concepts, re-implemented: stoploss, trailing stop, minimal ROI table, cooldown after a
losing trade, max open positions, daily loss limit and total-loss kill switch. Spot only, long or cash.
"""
import math

BUY_FEE, SELL_FEE = 0.002111, 0.004211   # Indodax IDR taker (incl. 0.21% PPh on sell)
MIN_STEP = 0.10                          # ignore resizes smaller than 10% of equity
DUST_IDR = 1000                          # position below this value counts as closed
MAX_LIST = 300


def new_book(start_idr):
    return {"idr": float(start_idr), "sol": 0.0, "start_idr": float(start_idr), "loss_base": float(start_idr),
            "position": None, "trades": [], "orders": [], "cooldown_until": 0, "reentry_block": False,
            "day": None, "day_start_eq": float(start_idr), "daily_block": None, "seq": 0}


def equity(b, px): return b["idr"] + b["sol"] * px


def exposure(b, px):
    e = equity(b, px)
    return b["sol"] * px / e if e > 0 else 0.0


def _id(b, prefix, ts):
    b["seq"] = b.get("seq", 0) + 1
    return f"{prefix}{int(ts)}-{b['seq']}"


def _push(lst, item):
    lst.append(item)
    del lst[:-MAX_LIST]


def buy(b, idr, px, ts, reason, executor=None):
    idr = min(idr, b["idr"])
    if idr < DUST_IDR: return None
    if executor:
        spent, got = executor("buy", px, idr=idr)
    else:
        spent, got = idr, idr * (1 - BUY_FEE) / px
    if got <= 0: return None
    b["idr"] -= spent; b["sol"] += got
    p = b["position"]
    if not p:
        p = b["position"] = {"id": _id(b, "T", ts), "opened_at": ts, "entry": spent / got, "sol": 0.0, "cost_idr": 0.0,
                             "invested_idr": 0.0, "realized_idr": 0.0, "peak": px, "trailing_active": False}
    p["cost_idr"] += spent; p["invested_idr"] += spent; p["sol"] = b["sol"]
    p["entry"] = p["cost_idr"] / p["sol"]
    p["peak"] = max(p["peak"], px)
    o = {"id": _id(b, "O", ts), "ts": ts, "side": "BUY", "price": px, "sol": round(got, 8), "idr": round(spent), "reason": reason}
    _push(b["orders"], o)
    return o


def sell(b, sol, px, ts, reason, risk=None, executor=None):
    sol = min(sol, b["sol"])
    if sol <= 0 or sol * px < 1: return None, None
    if executor:
        sold, got = executor("sell", px, sol=sol)
    else:
        sold, got = sol, sol * px * (1 - SELL_FEE)
    b["sol"] -= sold; b["idr"] += got
    o = {"id": _id(b, "O", ts), "ts": ts, "side": "SELL", "price": px, "sol": round(sold, 8), "idr": round(got), "reason": reason}
    _push(b["orders"], o)
    p, closed = b["position"], None
    if p:
        cost_part = p["entry"] * sold
        p["realized_idr"] += got - cost_part
        p["cost_idr"] = max(0.0, p["cost_idr"] - cost_part)
        p["sol"] = b["sol"]
        if b["sol"] * px < DUST_IDR:
            if b["sol"] > 0 and not executor:   # paper dust goes back to IDR
                b["idr"] += b["sol"] * px * (1 - SELL_FEE); b["sol"] = 0.0
            pnl = p["realized_idr"]
            closed = {"id": p["id"], "opened_at": p["opened_at"], "closed_at": ts, "entry": round(p["entry"]),
                      "exit": px, "invested_idr": round(p["invested_idr"]), "pnl_idr": round(pnl),
                      "pnl_pct": round(100 * pnl / p["invested_idr"], 2) if p["invested_idr"] else 0.0,
                      "exit_reason": reason}
            _push(b["trades"], closed)
            b["position"] = None
            if risk is not None and pnl < 0 and risk.get("cooldown_enabled"):
                b["cooldown_until"] = ts + 60 * risk.get("cooldown_minutes", 0)
    return o, closed


def close_all(b, px, ts, reason, risk=None, executor=None):
    if b["sol"] <= 0: return None, None
    return sell(b, b["sol"], px, ts, reason, risk, executor)


def risk_exit(b, risk, ts, close, low=None, high=None):
    """Check stoploss / trailing / ROI on an open position. Returns (reason, exit_price) or None."""
    p = b["position"]
    if not p: return None
    tick = low is None and high is None          # live tick: exit at the current price
    low = close if low is None else low
    high = close if high is None else high
    at = lambda trigger: close if tick else trigger
    if risk.get("stoploss_enabled"):
        stop = p["entry"] * (1 - risk["stoploss_pct"] / 100)
        if low <= stop: return "stoploss", at(stop)
    p["peak"] = max(p.get("peak", p["entry"]), high)
    if risk.get("trailing_enabled"):
        if p["peak"] >= p["entry"] * (1 + risk["trailing_activate_pct"] / 100):
            p["trailing_active"] = True
        if p.get("trailing_active"):
            tstop = p["peak"] * (1 - risk["trailing_pct"] / 100)
            if low <= tstop: return "trailing_stop", at(tstop)
    if risk.get("roi_enabled") and risk.get("minimal_roi"):
        mins = (ts - p["opened_at"]) / 60
        need = None
        for k, v in sorted(((int(k), v) for k, v in risk["minimal_roi"].items())):
            if mins >= k: need = v
        if need is not None:
            target_px = p["entry"] * (1 + need / 100) / (1 - SELL_FEE)
            if high >= target_px: return "roi", at(target_px)
    return None


def roll_day(b, day, px):
    """Start a new WIB day for the daily loss limit. Returns the previous day's (start, end) equity or None."""
    if b.get("day") == day: return None
    prev = (b.get("day"), b.get("day_start_eq"))
    b["day"], b["day_start_eq"], b["daily_block"] = day, equity(b, px), None
    return prev


def loss_check(b, risk, px):
    """Return ('daily'|'total', loss_pct) when a loss limit is hit, else None."""
    eq = equity(b, px)
    if risk.get("total_loss_enabled") and b.get("loss_base"):
        loss = 100 * (1 - eq / b["loss_base"])
        if loss >= risk["total_max_loss_pct"]: return "total", round(loss, 2)
    if risk.get("daily_loss_enabled") and b.get("day_start_eq"):
        loss = 100 * (1 - eq / b["day_start_eq"])
        if loss >= risk["daily_max_loss_pct"]: return "daily", round(loss, 2)
    return None


def caps(risk, feat):
    """Hard guardrails that always apply, whatever the strategy or AI says."""
    cap = risk["max_exposure_pct"] / 100
    cap = min(cap, (risk["vol_target_pct"] / 100) / max(feat.get("_vol") or 0.6, 1e-6))
    if risk.get("atr_guard_pct") and feat.get("_datr_pct") and feat["_datr_pct"] > risk["atr_guard_pct"]:
        cap *= 0.5
    return max(0.0, min(1.0, cap))


def entry_blocked(b, risk, ts):
    if b["position"]: return None
    if risk.get("max_open_positions", 1) < 1: return "max posisi = 0"
    if b.get("daily_block"): return "batas rugi harian tercapai"
    if risk.get("cooldown_enabled") and ts < b.get("cooldown_until", 0): return "cooldown setelah rugi"
    if b.get("reentry_block"): return "menunggu sinyal baru setelah stop/ROI"
    return None


def rebalance_to(b, target, px, ts, reason, risk=None, max_order_idr=None, executor=None):
    """Move the book toward target exposure. Returns (order, closed_trade) or (None, None) for HOLD."""
    eq = equity(b, px)
    if eq <= 0: return None, None
    cur = b["sol"] * px / eq
    if target <= 0.0005:
        if b["sol"] * px < DUST_IDR: return None, None
        sol = b["sol"]
        if max_order_idr and sol * px > max_order_idr: sol = max_order_idr / px
        return sell(b, sol, px, ts, reason, risk, executor)
    if abs(target - cur) < MIN_STEP: return None, None
    delta = (target - cur) * eq
    if max_order_idr: delta = max(-max_order_idr, min(max_order_idr, delta))
    if delta > 0: return buy(b, delta, px, ts, reason, executor), None
    return sell(b, -delta / px, px, ts, reason, risk, executor)
