"""Bot configuration: config/bot-config.json (non-secret) > repo variables (env) > defaults.

Every field is validated and clamped. A missing, unreadable or corrupted file never crashes the bot:
bad values fall back to defaults and a warning is recorded.
Secrets (API keys) never live here; they are GitHub Actions secrets.
"""
import copy, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("BOT_CONFIG") or os.path.join(HERE, "..", "config", "bot-config.json")

PROVIDERS = ("nafnaf", "gonka", "arcee")
STRATEGIES = ("ai_supertrend", "ema_rsi", "bollinger")
TIMEFRAMES = ("15m", "1h", "4h", "1d")
CONFIDENCES = ("low", "medium", "high")
VENUES = ("both", "hyperliquid", "indodax")   # which venue(s) the bot runs; Hyperliquid is ALWAYS paper
PERP_TFS = ("15m", "1h", "4h", "1d")
PAIR_RE = re.compile(r"^[A-Z0-9]{2,12}$")
HARD_MAX_LEVERAGE = 5

DEFAULTS = {
    "version": 3,
    "enabled": True,
    "venue": "both",                      # "both" | "hyperliquid" | "indodax"                      # master switch; OFF -> scheduled runs exit without trading
    "mode": "paper",                      # "paper" | "live"  (live also needs Indodax secrets + passes runtime guardrails)
    "close_position_request": "",         # app sets a new token -> bot closes the open position once
    "live_exit_request": "",              # app STOP + sell: bot sells the LIVE book once (even after mode -> paper)
    "strategy": {"name": "ai_supertrend", "timeframe": "1d",
                 "trend_filter_ma": 200},          # new longs only when daily close > SMA(N) of daily closes; 0 = off
    "ai": {
        "enabled": True,                  # AI filter/sizer on top of the strategy
        "providers": [
            {"name": "nafnaf", "enabled": True, "model": "qwen3.8-flash-next"},
            {"name": "gonka", "enabled": True, "model": "zai-org/GLM-5.3-Flash"},
            {"name": "arcee", "enabled": True, "model": "deepseek/deepseek-v4-flash-latest"},
        ],
        "temperature": 0.2,
        "min_confidence": "low",          # below this the strategy rule is used instead of the AI
        "extra_instruction": "",
    },
    "risk": {
        "max_exposure_pct": 100,
        "vol_target_pct": 60,             # exposure cap = vol_target / realised annual vol (daily candles)
        "atr_guard_pct": 0,               # 0 = off; if daily ATR% > this, max exposure is halved
        "stoploss_enabled": True, "stoploss_pct": 10,
        "trailing_enabled": False, "trailing_pct": 5, "trailing_activate_pct": 3,
        "roi_enabled": False, "minimal_roi": {"0": 20, "1440": 10, "4320": 5},   # minutes -> % profit
        "cooldown_enabled": True, "cooldown_minutes": 1440,
        "max_open_positions": 1,          # single pair (SOL/IDR): 0 = no new entries, 1 = normal
        "daily_loss_enabled": True, "daily_max_loss_pct": 5,
        "total_loss_enabled": True, "total_max_loss_pct": 25,
        "kill_pause_days": 14,            # total kill switch (PAPER) pauses entries N days, then resumes with a new baseline; 0 = stop bot
    },
    "paper": {"start_idr": 1_000_000},
    "live": {"max_capital_idr": 500_000, "max_order_idr": 250_000},
    "backtest": {"days": 365},
    # Hyperliquid perps, PAPER ONLY (there is no live Hyperliquid order code in this bot)
    "perps": {
        "pairs": ["SOL", "BTC", "ETH"],
        "timeframe": "4h",                # analyst agents run when a candle of this timeframe closes
        "paper_start_usd": 250,
        "max_leverage": 3,                # hard cap 5x (and never above the exchange max)
        "risk_per_trade_pct": 1.0,        # loss at the stop = this % of equity
        "max_positions": 2,               # max concurrent positions
        "stop_atr_mult": 2.0,             # stop distance = N x ATR(14) of the timeframe
        "take_profit_r": 2.0,             # take-profit at N x stop distance (0 = no TP)
        "breakeven_at_r": 1.0,            # move stop to entry after +N R (0 = off)
        "liq_buffer_mult": 2.0,           # liquidation must be >= N x stop distance away from entry
        "entry_threshold": 0.25,          # coordinator score needed for long/short (0..1)
        "allow_short": True,
        "use_llm": True,                  # LLM answers the 6 analysts in one batched call; rules otherwise
        "daily_loss_enabled": True, "daily_max_loss_pct": 3,
        "kill_switch_enabled": True, "total_max_loss_pct": 15,
        "kill_pause_days": 14,            # kill switch pauses entries N days, then resumes with a new baseline; 0 = until reset
        "reset_request": "",              # app sets a new token -> paper perps book reset once
    },
}


def _num(v, lo, hi, default, integer=False):
    try:
        if isinstance(v, bool): raise ValueError
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")): raise ValueError
    except Exception:
        return default, False
    x = max(lo, min(hi, x))
    return (int(round(x)) if integer else x), True


def _bool(v, default):
    return (v, True) if isinstance(v, bool) else (default, v is None)


def validate(raw, env=None):
    """Return (config, warnings). Never raises."""
    env = os.environ if env is None else env
    cfg, warn = copy.deepcopy(DEFAULTS), []

    # repo variables sit between defaults and the file
    for p in cfg["ai"]["providers"]:
        m = (env.get(f"{p['name'].upper()}_MODEL") or "").strip()
        if m: p["model"] = m[:120]
    if (env.get("BOT_MODE") or "").strip().lower() in ("paper", "live"):
        cfg["mode"] = env["BOT_MODE"].strip().lower()

    if not isinstance(raw, dict):
        if raw is not None: warn.append("config bukan objek JSON; pakai default")
        return cfg, warn

    def sub(d, k):
        v = d.get(k) if isinstance(d, dict) else None
        return v if isinstance(v, dict) else {}

    def setb(dst, src, k):
        if k in src:
            v, ok = _bool(src[k], dst[k]); dst[k] = v
            if not ok: warn.append(f"{k} harus true/false")

    def setn(dst, src, k, lo, hi, integer=False):
        if k in src:
            v, ok = _num(src[k], lo, hi, dst[k], integer)
            if ok and float(src[k]) != v: warn.append(f"{k} dibatasi ke {v}")
            if not ok: warn.append(f"{k} tidak valid; pakai {dst[k]}")
            dst[k] = v

    setb(cfg, raw, "enabled")
    if "mode" in raw:
        m = str(raw["mode"]).strip().lower()
        if m in ("paper", "live"): cfg["mode"] = m
        else: warn.append("mode tidak dikenal; pakai paper"); cfg["mode"] = "paper"
    for k in ("close_position_request", "live_exit_request"):
        if isinstance(raw.get(k), (str, int)) and not isinstance(raw.get(k), bool):
            cfg[k] = str(raw[k])[:64]

    st = sub(raw, "strategy")
    if st.get("name") in STRATEGIES: cfg["strategy"]["name"] = st["name"]
    elif "name" in st: warn.append("strategi tidak dikenal; pakai ai_supertrend")
    if st.get("timeframe") in TIMEFRAMES: cfg["strategy"]["timeframe"] = st["timeframe"]
    elif "timeframe" in st: warn.append("timeframe tidak dikenal; pakai 1d")
    setn(cfg["strategy"], st, "trend_filter_ma", 0, 400, integer=True)

    ai = sub(raw, "ai")
    setb(cfg["ai"], ai, "enabled")
    setn(cfg["ai"], ai, "temperature", 0.0, 1.5)
    if ai.get("min_confidence") in CONFIDENCES: cfg["ai"]["min_confidence"] = ai["min_confidence"]
    if isinstance(ai.get("extra_instruction"), str):
        cfg["ai"]["extra_instruction"] = ai["extra_instruction"].strip()[:500]
    if isinstance(ai.get("providers"), list):
        by = {p["name"]: p for p in cfg["ai"]["providers"]}
        order = []
        for p in ai["providers"]:
            if not isinstance(p, dict) or p.get("name") not in PROVIDERS or p["name"] in order: continue
            d = by[p["name"]]
            if isinstance(p.get("enabled"), bool): d["enabled"] = p["enabled"]
            if isinstance(p.get("model"), str) and p["model"].strip(): d["model"] = p["model"].strip()[:120]
            order.append(p["name"])
        order += [n for n in PROVIDERS if n not in order]
        cfg["ai"]["providers"] = [by[n] for n in order]

    r, R = sub(raw, "risk"), cfg["risk"]
    setn(R, r, "max_exposure_pct", 0, 100)
    setn(R, r, "vol_target_pct", 10, 300)
    setn(R, r, "atr_guard_pct", 0, 50)
    for k in ("stoploss_enabled", "trailing_enabled", "roi_enabled", "cooldown_enabled",
              "daily_loss_enabled", "total_loss_enabled"):
        setb(R, r, k)
    setn(R, r, "stoploss_pct", 0.5, 50)
    setn(R, r, "trailing_pct", 0.5, 50)
    setn(R, r, "trailing_activate_pct", 0, 100)
    setn(R, r, "cooldown_minutes", 0, 10080, integer=True)
    setn(R, r, "max_open_positions", 0, 1, integer=True)
    setn(R, r, "daily_max_loss_pct", 0.5, 50)
    setn(R, r, "total_max_loss_pct", 1, 90)
    setn(R, r, "kill_pause_days", 0, 365, integer=True)
    if "minimal_roi" in r:
        roi = {}
        if isinstance(r["minimal_roi"], dict):
            for k, v in list(r["minimal_roi"].items())[:8]:
                kk, ok1 = _num(k, 0, 525600, None, integer=True)
                vv, ok2 = _num(v, 0, 1000, None)
                if ok1 and ok2: roi[str(kk)] = vv
        if roi: R["minimal_roi"] = dict(sorted(roi.items(), key=lambda kv: int(kv[0])))
        else: warn.append("minimal_roi tidak valid; pakai default")

    setn(cfg["paper"], sub(raw, "paper"), "start_idr", 100_000, 10_000_000_000, integer=True)
    lv = sub(raw, "live")
    setn(cfg["live"], lv, "max_capital_idr", 50_000, 10_000_000_000, integer=True)
    setn(cfg["live"], lv, "max_order_idr", 10_000, 10_000_000_000, integer=True)
    setn(cfg["backtest"], sub(raw, "backtest"), "days", 30, 365, integer=True)

    if "venue" in raw:
        v = str(raw["venue"]).strip().lower()
        if v in VENUES: cfg["venue"] = v
        else: warn.append("venue tidak dikenal; pakai both")
    pr, P = sub(raw, "perps"), cfg["perps"]
    if "pairs" in pr:
        src = pr["pairs"]
        if isinstance(src, str): src = src.split(",")
        pairs = []
        if isinstance(src, list):
            for x in src:
                x = str(x).strip().upper().replace("-PERP", "").replace("USDC", "").replace("USD", "") if isinstance(x, (str, int)) else ""
                if PAIR_RE.match(x) and x not in pairs: pairs.append(x)
        if pairs: P["pairs"] = pairs[:8]
        else: warn.append("perps.pairs kosong/tidak valid; pakai SOL, BTC, ETH")
    if pr.get("timeframe") in PERP_TFS: P["timeframe"] = pr["timeframe"]
    elif "timeframe" in pr: warn.append("perps.timeframe tidak dikenal; pakai 4h")
    setn(P, pr, "paper_start_usd", 20, 10_000_000)
    setn(P, pr, "max_leverage", 1, HARD_MAX_LEVERAGE, integer=True)
    setn(P, pr, "risk_per_trade_pct", 0.1, 5)
    setn(P, pr, "max_positions", 0, 8, integer=True)
    setn(P, pr, "stop_atr_mult", 0.5, 10)
    setn(P, pr, "take_profit_r", 0, 20)
    setn(P, pr, "breakeven_at_r", 0, 10)
    setn(P, pr, "liq_buffer_mult", 1, 10)
    setn(P, pr, "entry_threshold", 0.05, 0.95)
    setn(P, pr, "daily_max_loss_pct", 0.5, 50)
    setn(P, pr, "total_max_loss_pct", 1, 90)
    setn(P, pr, "kill_pause_days", 0, 365, integer=True)
    for k in ("allow_short", "use_llm", "daily_loss_enabled", "kill_switch_enabled"):
        setb(P, pr, k)
    if isinstance(pr.get("reset_request"), (str, int)) and not isinstance(pr.get("reset_request"), bool):
        P["reset_request"] = str(pr["reset_request"])[:64]
    return cfg, warn


def load_config(path=None, env=None):
    path = path or CONFIG_PATH
    raw, warn = None, []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f: raw = json.load(f)
        except Exception as e:
            warn.append(f"config rusak ({type(e).__name__}); pakai default")
            raw = None
    cfg, w2 = validate(raw, env)
    file_ok = not warn and (raw is None or isinstance(raw, dict))
    if not file_ok:
        cfg["mode"] = "paper"          # a broken config can never enable live trading
    return cfg, warn + w2, file_ok


def save_config(cfg, path=None):
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False); f.write("\n")
    os.replace(tmp, path)
