"use strict";
/* Core: state loading, router, dashboard / trades / backtest / notification rendering. */
const $ = id => document.getElementById(id);
const REPO = "Maftuuh1922/sol-ai-bot";
const RAW = `https://raw.githubusercontent.com/${REPO}/main/`;
const RAW_STATE = RAW + "docs/data/state.json";
const RAW_BT = RAW + "docs/data/backtest.json";
const APK_URL = `https://github.com/${REPO}/releases/latest/download/sol-ai-bot.apk`;
const IS_APP = !!(window.Capacitor && (typeof window.Capacitor.isNativePlatform !== "function" || window.Capacitor.isNativePlatform()));
const ON_PAGES = /\.github\.io$/i.test(location.hostname);
const CACHE_KEY = "solbot-state-v2";
const rp = v => v == null || isNaN(v) ? "—" : (v < 0 ? "-" : "") + "Rp" + Math.round(Math.abs(v)).toLocaleString("id-ID");
const pct = v => v == null || isNaN(v) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(2).replace(".", ",") + "%";
const num = (v, d = 4) => v == null ? "—" : Number(v).toLocaleString("id-ID", { maximumFractionDigits: d });
const wib = x => {
  if (!x) return "—";
  const d = typeof x === "number" ? new Date(x * 1000) : new Date(x);
  if (isNaN(d)) return String(x);
  return d.toLocaleString("id-ID", { timeZone: "Asia/Jakarta", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + " WIB";
};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const plugin = n => { const C = window.Capacitor; if (!IS_APP || !C) return null; return (C.Plugins && C.Plugins[n]) || (C.registerPlugin ? C.registerPlugin(n) : null); };

const SB = window.SB = { state: null, bt: null, livePx: null, view: "home", hooks: { state: [], view: [] } };

function toast(msg, ms = 3200) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}
function modal(html, cls = "") {
  const m = $("modal"), b = $("mbox");
  b.className = "mbox " + cls; b.innerHTML = html; m.hidden = false;
  return { el: b, close: () => { m.hidden = true; b.innerHTML = ""; } };
}
SB.toast = toast; SB.modal = modal; SB.rp = rp; SB.pct = pct; SB.esc = esc; SB.wib = wib; SB.plugin = plugin; SB.IS_APP = IS_APP;
$("modal").addEventListener("click", e => { if (e.target.id === "modal") { $("modal").hidden = true; } });

/* ---------------- router ---------------- */
function show(v) {
  SB.view = v;
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== "v-" + v;
  for (const b of document.querySelectorAll("#bnav button")) b.classList.toggle("on", b.dataset.v === v);
  if (location.hash.slice(1) !== v) history.replaceState(null, "", v === "home" ? location.pathname + location.search : "#" + v);
  window.scrollTo(0, 0);
  SB.hooks.view.forEach(f => { try { f(v); } catch (e) { console.error(e); } });
}
SB.show = show;
for (const b of document.querySelectorAll("#bnav button")) b.addEventListener("click", () => show(b.dataset.v));
$("gear").addEventListener("click", () => show("settings"));
const HASHES = { pengaturan: "settings", settings: "settings", trade: "trades", trades: "trades", backtest: "backtest", notif: "notif" };

/* ---------------- helpers on the v2 state ---------------- */
function book(s) {
  const m = (s.mode || "PAPER").toLowerCase();
  if (s.books) return s.books[m] || s.books.paper;
  return s.ai ? Object.assign({ trades: [], orders: [], position: null, start_idr: s.start_idr || 1e6 }, s.ai) : null;
}
SB.book = book;
const eqOf = (w, px) => w ? (w.idr || 0) + (w.sol || 0) * px : null;
function setPc(el, eq, start) {
  if (eq == null || !start) { el.textContent = "—"; el.className = "pc"; return; }
  const p = (eq / start - 1) * 100; el.textContent = pct(p); el.className = "pc " + (p >= 0 ? "up" : "dn");
}

/* ---------------- render: beranda ---------------- */
function renderHome(s, px) {
  const mode = (s.mode || "PAPER").toUpperCase();
  $("mode").textContent = mode; $("mode").className = "badge " + mode.toLowerCase();
  $("liveBar").hidden = mode !== "LIVE";
  const kill = s.kill && s.kill.active;
  $("killNote").hidden = !kill;
  if (kill) $("killNote").textContent = `⛔ Kill switch ${wib(s.kill.at)}: rugi ${s.kill.loss_pct}% (${s.kill.kind === "daily" ? "harian" : "total"}). Bot dimatikan & kembali ke PAPER. Nyalakan lagi lewat tombol Bot aktif.`;
  const refused = (s.requested_mode || "") === "LIVE" && mode !== "LIVE";
  $("liveRefused").hidden = !refused;
  if (refused) {
    const ev = (s.events || []).slice().reverse().find(e => e.title && e.title.startsWith("Live ditolak"));
    $("liveRefused").textContent = "Mode LIVE diminta tapi ditolak bot → tetap PAPER" + (ev ? ": " + ev.body : ".");
  }
  const b = book(s), start = (b && b.start_idr) || s.start_idr || 1e6;
  const eAi = eqOf(b, px), eSt = eqOf(s.bench, px), eHd = s.hold && s.hold.sol ? s.hold.sol * px : null;
  $("eqAi").textContent = rp(eAi); setPc($("pcAi"), eAi, start);
  $("eqSt").textContent = rp(eSt); setPc($("pcSt"), eSt, s.start_idr || start);
  $("eqHd").textContent = rp(eHd); setPc($("pcHd"), eHd, s.start_idr || start);
  if (b && px) $("expAi").textContent = "Eksposur " + Math.round(100 * (b.sol * px) / (eAi || 1)) + "%";
  if (s.bench && px) $("expSt").textContent = "Eksposur " + Math.round(100 * (s.bench.sol * px) / (eSt || 1)) + "%";
  $("startNote").textContent = `Modal awal: ${rp(start)} (${mode === "LIVE" ? "batas modal live" : "simulasi"}) · Supertrend & Hold selalu simulasi`;

  // bot switch + position
  const enabled = s.status !== "disabled";
  if (!SB.pendingEnabled) $("botEnabled").checked = SB.cfg ? !!SB.cfg.enabled : enabled;
  const cfgOn = SB.cfg ? SB.cfg.enabled : enabled;
  $("botStatus").innerHTML = cfgOn
    ? `<span class="up">● Aktif</span> · ${esc(stratName(s))} · cek tiap ±15 mnt`
    : `<span class="dn">● Bot dimatikan</span> — jadwal tetap jalan, tidak ada trading`;
  const p = b && b.position;
  $("posLine").hidden = !p;
  if (p) {
    const up = (px / p.entry - 1) * 100;
    $("posText").innerHTML = `Posisi ${num(p.sol)} SOL @ ${rp(p.entry)} · <span class="${up >= 0 ? "up" : "dn"}">${pct(up)}</span>`;
  }

  const log = s.log || [];
  const d = log[log.length - 1];
  $("decErr").hidden = true;
  if (d) {
    $("decDate").textContent = (d.tf ? d.tf + " · " : "") + (d.candle || d.date || "");
    const label = d.provider_label || providerLabel(d.provider);
    const isFb = /fallback|^rule/.test(d.provider || "");
    $("decAi").textContent = (isFb ? "" : "AI: ") + label;
    $("decAi").className = "aiused" + (isFb ? " fb" : "");
    $("decTarget").textContent = Math.round((d.final ?? 0) * 100) + "%";
    $("decConf").textContent = { low: "Rendah", medium: "Sedang", high: "Tinggi", fallback: "Cadangan", rule: "Aturan" }[d.confidence] || d.confidence || "—";
    $("decAct").textContent = d.action || "—";
    $("decReason").textContent = d.reason || "—";
    const meta = [];
    if (d.strategy) meta.push("Strategi " + d.strategy);
    if (d.ai_target != null && Math.abs(d.ai_target - d.final) > 0.001) meta.push(`Usulan AI ${Math.round(d.ai_target * 100)}% → ${Math.round(d.final * 100)}% (batas ${Math.round(d.cap * 100)}%)`);
    else if (d.cap != null) meta.push(`Batas eksposur ${Math.round(d.cap * 100)}%`);
    if (d.blocked) meta.push("Entry diblok: " + d.blocked);
    if (d.ran_at) meta.push(wib(d.ran_at));
    $("decMeta").textContent = meta.join(" · ");
    if (d.error) { $("decErr").hidden = false; $("decErr").textContent = "Catatan error: " + d.error; }
  }

  const m = s.market || {};
  const up = (m.supertrend || (d && d.supertrend)) === "UP";
  if (m.supertrend || d) { $("stDir").textContent = up ? "▲ NAIK (UP)" : "▼ TURUN (DOWN)"; $("stDir").className = "pill " + (up ? "up" : "dn"); }
  $("stLine").textContent = rp(m.supertrend_line_idr);
  const rows = [
    ["Timeframe", m.timeframe], ["RSI 14", m.rsi14],
    ["EMA 50", rp(m.ema50_idr)], ["EMA 200", rp(m.ema200_idr)],
    ["ATR 14 harian", m.daily_atr14_pct != null ? m.daily_atr14_pct + "%" : (m.atr14_pct != null ? m.atr14_pct + "%" : null)],
    ["Volatilitas 30h (thn)", m.realized_vol_30d_annual_pct != null ? m.realized_vol_30d_annual_pct + "%" : null],
    ["Return 7h", m.ret_7d_pct != null ? pct(m.ret_7d_pct) : null],
    ["Return 30h", m.ret_30d_pct != null ? pct(m.ret_30d_pct) : null],
    ["Dari puncak 365h", m.drawdown_from_365d_high_pct != null ? pct(m.drawdown_from_365d_high_pct) : null],
  ].filter(r => r[1] != null && r[1] !== "—");
  $("mkt").innerHTML = rows.map(r => `<div><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("");
  drawEquity(s, px, eAi, eSt, eHd);
}
function stratName(s) {
  const n = (SB.cfg && SB.cfg.strategy && SB.cfg.strategy.name) || ((s.log || []).slice(-1)[0] || {}).strategy || "ai_supertrend";
  const tf = (SB.cfg && SB.cfg.strategy && SB.cfg.strategy.timeframe) || ((s.market || {}).timeframe) || "1d";
  return ({ ai_supertrend: "AI + Supertrend", ema_rsi: "EMA cross + RSI", bollinger: "Bollinger + RSI" }[n] || n) + " · " + tf;
}
function providerLabel(p) {
  if (!p) return "—";
  if (p.startsWith("fallback")) return "Cadangan · Supertrend";
  const [n, ...m] = p.split(":");
  return ({ nafnaf: "NafNaf", gonka: "Gonka", arcee: "Arcee", rule: "Aturan" }[n] || n) + " · " + m.join(":");
}

let eqChart;
function drawEquity(s, px, eAi, eSt, eHd) {
  if (!window.Chart) { setTimeout(() => drawEquity(s, px, eAi, eSt, eHd), 300); return; }
  const pts = (s.equity || []).slice();
  const start = s.start_idr || 1e6;
  const lab = p => p.t ? new Date(p.t * 1000).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", day: "numeric", month: "short", hour: pts.length > 60 ? undefined : "2-digit" }) : p.date;
  const labels = ["Mulai"].concat(pts.map(lab));
  const data = pts.slice();
  if (SB.livePx && eAi != null) { labels.push("Sekarang"); data.push({ ai: eAi, bench: eSt, hold: eHd }); }
  const ser = k => [start].concat(data.map(p => p[k] ?? null));
  $("chartNote").textContent = pts.length < 2 ? "Kurva terisi otomatis setiap bot mengevaluasi candle baru." : "";
  const ds = [
    { label: "Bot (paper)", data: ser("ai"), borderColor: "#9945ff" },
    { label: "Supertrend", data: ser("bench"), borderColor: "#5a8cf0" },
    { label: "Hold", data: ser("hold"), borderColor: "#f5b942" },
  ];
  if (pts.some(p => p.live != null)) ds.push({ label: "Live", data: [null].concat(data.map(p => p.live ?? null)), borderColor: "#ff5c7a" });
  ds.forEach(d => Object.assign(d, { backgroundColor: d.borderColor, tension: .25, pointRadius: data.length > 40 ? 0 : 3, borderWidth: 2, spanGaps: true }));
  const vals = ds.flatMap(d => d.data).filter(v => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = Math.max((hi - lo) * 0.1, start * 0.01);
  if (eqChart) eqChart.destroy();
  eqChart = new Chart($("chart"), {
    type: "line", data: { labels, datasets: ds },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#c8d2e3", boxWidth: 12 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${rp(c.parsed.y)}` } } },
      scales: { x: { ticks: { color: "#8b97ad", maxTicksLimit: 5 }, grid: { color: "#1b2336" } },
        y: { suggestedMin: lo - pad, suggestedMax: hi + pad, ticks: { color: "#8b97ad", maxTicksLimit: 6, callback: v => (v / 1e6).toFixed(hi - lo < start * 0.05 ? 3 : 2).replace(".", ",") + " jt" }, grid: { color: "#1b2336" } } } },
  });
}

/* ---------------- render: trade ---------------- */
const REASON = { stoploss: "stop-loss", trailing_stop: "trailing", roi: "target ROI", signal: "sinyal", kill_total: "kill switch total", kill_daily: "batas rugi harian" };
function renderTrades(s, px) {
  const b = book(s); if (!b) return;
  $("tBook").textContent = (s.mode || "PAPER").toUpperCase();
  $("tBook").className = "tag " + ((s.mode || "") === "LIVE" ? "dn" : "");
  const p = b.position, cfg = SB.cfg || {}, R = cfg.risk || {};
  if (p) {
    const val = p.sol * px, un = val * (1 - 0.004211) - p.cost_idr, unp = 100 * un / (p.cost_idr || 1);
    const stop = R.stoploss_enabled ? p.entry * (1 - R.stoploss_pct / 100) : null;
    const tr = R.trailing_enabled && p.trailing_active ? p.peak * (1 - R.trailing_pct / 100) : null;
    const held = Math.max(0, Date.now() / 1000 - p.opened_at);
    $("openPos").innerHTML = `<div class="posgrid">
      <div><span>Ukuran</span>${num(p.sol)} SOL</div><div><span>Nilai sekarang</span>${rp(val)}</div>
      <div><span>Harga masuk (rata2, +fee)</span>${rp(p.entry)}</div><div><span>Harga sekarang</span>${rp(px)}</div>
      <div><span>PnL belum terealisasi</span><b class="${un >= 0 ? "up" : "dn"}">${rp(un)} (${pct(unp)})</b></div><div><span>Dipegang</span>${held > 86400 ? (held / 86400).toFixed(1) + " hari" : (held / 3600).toFixed(1) + " jam"}</div>
      <div><span>Stop-loss</span>${stop ? rp(stop) : "mati"}</div><div><span>Trailing</span>${R.trailing_enabled ? (tr ? rp(tr) : `aktif di +${R.trailing_activate_pct}%`) : "mati"}</div>
      <div><span>Puncak sejak masuk</span>${rp(p.peak)}</div><div><span>Dibuka</span>${wib(p.opened_at)}</div></div>`;
  } else $("openPos").innerHTML = `<span class="muted">Tidak ada posisi terbuka. Saldo ${rp(b.idr)}.</span>`;
  const tr = b.trades || [], wins = tr.filter(t => t.pnl_idr > 0);
  const eq = eqOf(b, px), start = b.start_idr || s.start_idr || 1e6;
  const realized = tr.reduce((a, t) => a + t.pnl_idr, 0);
  const kv = [["Ekuitas", rp(eq)], ["Total PnL", `${rp(eq - start)} (${pct((eq / start - 1) * 100)})`], ["PnL terealisasi", rp(realized)],
    ["Jumlah trade", tr.length], ["Win rate", tr.length ? Math.round(100 * wins.length / tr.length) + "%" : "—"],
    ["Rata-rata trade", tr.length ? pct(tr.reduce((a, t) => a + t.pnl_pct, 0) / tr.length) : "—"]];
  $("pnlKv").innerHTML = kv.map(r => `<div><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("");
  $("tradeList").innerHTML = tr.length ? tr.slice().reverse().map(t => `<li class="hi"><div class="row"><b>${wib(t.closed_at)}</b>
    <span class="tag ${t.pnl_idr >= 0 ? "up" : "dn"}">${pct(t.pnl_pct)}</span></div>
    <div class="muted small">Masuk ${rp(t.entry)} → keluar ${rp(t.exit)} · ${rp(t.pnl_idr)} · ${esc(REASON[t.exit_reason] || t.exit_reason)}</div></li>`).join("")
    : `<li class="muted">Belum ada trade selesai.</li>`;
  const od = b.orders || [];
  $("orderList").innerHTML = od.length ? od.slice().reverse().slice(0, 60).map(o => `<li class="hi"><div class="row"><b>${wib(o.ts)}</b>
    <span class="tag ${o.side === "BUY" ? "up" : "dn"}">${o.side}</span></div>
    <div class="muted small">${num(o.sol)} SOL @ ${rp(o.price)} · ${rp(o.idr)} · ${esc(o.reason)}</div></li>`).join("")
    : `<li class="muted">Belum ada order.</li>`;
  const log = s.log || [];
  if (log.length) $("hist").innerHTML = log.slice().reverse().slice(0, 80).map(e => `<li class="hi">
      <div class="row"><b>${esc(e.candle || e.date)}</b><span class="tag ${/^BUY/.test(e.action) ? "up" : /^SELL/.test(e.action) ? "dn" : ""}">${esc(e.action || "HOLD")}</span></div>
      <div class="muted small">${esc(e.provider_label || providerLabel(e.provider))} · Close ${rp(e.close)} · Target ${Math.round((e.final ?? 0) * 100)}% · ST <span class="${e.supertrend === "UP" ? "up" : "dn"}">${esc(e.supertrend)}</span></div>
      ${e.reason ? `<div class="hr">${esc(e.reason)}</div>` : ""}</li>`).join("");
}

/* ---------------- render: backtest ---------------- */
let btChart;
function renderBacktest(bt) {
  if (!bt || !bt.stats) return;
  const st = bt.stats;
  $("btMeta").textContent = `${bt.strategy} · ${bt.timeframe} · ${bt.days} hari (${bt.bars} candle) · dibuat ${wib(bt.generated_at)}. ${bt.note || ""}`;
  const kv = [["Return bot", pct(st.return_pct)], ["Return Hold", pct(st.hold_return_pct)], ["Max drawdown", pct(st.max_drawdown_pct)],
    ["Drawdown Hold", pct(st.hold_max_drawdown_pct)], ["Jumlah trade", st.trades], ["Win rate", st.win_rate_pct != null ? st.win_rate_pct + "%" : "—"],
    ["Rata-rata trade", pct(st.avg_trade_pct)], ["Profit factor", st.profit_factor ?? "—"], ["Ekuitas akhir", rp(st.final_equity)],
    ["Kill switch", st.killed_on ? "kena " + st.killed_on : "tidak"]];
  $("btKv").innerHTML = kv.map(r => `<div><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("");
  const cmp = bt.compare || {};
  $("btCmp").innerHTML = `<table><tr><th>Strategi</th><th>Return</th><th>MDD</th><th>Trade</th><th>Win</th></tr>` +
    Object.entries(cmp).map(([k, v]) => `<tr class="${k === bt.strategy ? "me" : ""}"><td>${esc(k)}</td><td class="${v.return_pct >= 0 ? "up" : "dn"}">${pct(v.return_pct)}</td><td>${pct(v.max_drawdown_pct)}</td><td>${v.trades}</td><td>${v.win_rate_pct != null ? v.win_rate_pct + "%" : "—"}</td></tr>`).join("") +
    `<tr><td>Hold</td><td class="${st.hold_return_pct >= 0 ? "up" : "dn"}">${pct(st.hold_return_pct)}</td><td>${pct(st.hold_max_drawdown_pct)}</td><td>1</td><td>—</td></tr></table>`;
  const tr = bt.trades || [];
  $("btTrades").innerHTML = tr.length ? tr.slice().reverse().slice(0, 40).map(t => `<li class="hi"><div class="row"><b>${wib(t.opened_at)} → ${wib(t.closed_at)}</b>
    <span class="tag ${t.pnl_idr >= 0 ? "up" : "dn"}">${pct(t.pnl_pct)}</span></div><div class="muted small">${rp(t.entry)} → ${rp(t.exit)} · ${esc(REASON[t.exit_reason] || t.exit_reason)}</div></li>`).join("") : `<li class="muted">Tidak ada trade.</li>`;
  const draw = () => {
    if (!window.Chart) return setTimeout(draw, 300);
    if (btChart) btChart.destroy();
    const pts = bt.equity || [];
    btChart = new Chart($("btChart"), { type: "line",
      data: { labels: pts.map(p => new Date(p.t * 1000).toLocaleDateString("id-ID", { day: "numeric", month: "short" })),
        datasets: [{ label: "Bot", data: pts.map(p => p.bot), borderColor: "#9945ff", backgroundColor: "#9945ff" },
                   { label: "Hold", data: pts.map(p => p.hold), borderColor: "#f5b942", backgroundColor: "#f5b942" }]
          .map(d => Object.assign(d, { pointRadius: 0, borderWidth: 2, tension: .2 })) },
      options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#c8d2e3", boxWidth: 12 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${rp(c.parsed.y)}` } } },
        scales: { x: { ticks: { color: "#8b97ad", maxTicksLimit: 5 }, grid: { color: "#1b2336" } }, y: { ticks: { color: "#8b97ad", callback: v => (v / 1e6).toFixed(2).replace(".", ",") + " jt" }, grid: { color: "#1b2336" } } } } });
  };
  draw();
}

/* ---------------- loading ---------------- */
function withTimeout(p, ms) { return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]); }
SB.withTimeout = withTimeout;
async function fetchJson(url) {
  const r = await withTimeout(fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" }), 12000);
  if (!r.ok) throw new Error("HTTP " + r.status);
  const j = await r.json();
  if (!j || typeof j !== "object") throw new Error("data kosong");
  return j;
}
SB.fetchJson = fetchJson;
async function loadState() {
  const urls = ON_PAGES ? ["data/state.json", RAW_STATE] : [RAW_STATE, "data/state.json"];
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch (e) {}
  for (const u of urls) {
    if (IS_APP && u === "data/state.json" && cached) return { s: cached, src: "cache" };
    try {
      const s = await fetchJson(u);
      try { localStorage.setItem(CACHE_KEY, JSON.stringify(s)); } catch (e) {}
      return { s, src: u === RAW_STATE ? "github" : "local" };
    } catch (e) { /* next */ }
  }
  if (cached) return { s: cached, src: "cache" };
  throw new Error("Gagal memuat data bot");
}
async function loadBacktest() {
  for (const u of (ON_PAGES ? ["data/backtest.json", RAW_BT] : [RAW_BT, "data/backtest.json"])) {
    try { return await fetchJson(u); } catch (e) {}
  }
  return null;
}

function renderAll() {
  const s = SB.state; if (!s) return;
  const lastClose = s.price || (s.log && s.log.length ? s.log[s.log.length - 1].close : (s.market || {}).close_idr);
  const px = SB.livePx || lastClose;
  if (!SB.livePx) { $("price").textContent = rp(px); $("priceSrc").textContent = "· dari bot"; }
  renderHome(s, px); renderTrades(s, px);
}
SB.renderAll = renderAll;

let lastLoad = 0, loading = false;
async function main() {
  if (loading) return;
  loading = true; lastLoad = Date.now();
  $("refresh").classList.add("spin");
  try {
    let res;
    try { res = await loadState(); }
    catch (e) { $("decReason").textContent = "Gagal memuat data bot. Cek koneksi internet lalu tekan ⟳."; return; }
    SB.state = res.s;
    $("offline").hidden = res.src !== "cache";
    renderAll();
    SB.hooks.state.forEach(f => { try { f(res.s); } catch (e) { console.error(e); } });
    loadBacktest().then(bt => { SB.bt = bt; renderBacktest(bt); });
  } finally {
    loading = false; $("refresh").classList.remove("spin");
  }
}
SB.reload = main;
$("refresh").addEventListener("click", main);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - lastLoad > 30000) main();
});
if (!IS_APP) { const a = $("apk"); if (a) { a.href = APK_URL; a.hidden = false; } }
window.addEventListener("DOMContentLoaded", () => {
  const h = HASHES[location.hash.slice(1)];
  if (h) show(h);
  main();
});
if (!IS_APP && "serviceWorker" in navigator && /^https?:$/.test(location.protocol)) navigator.serviceWorker.register("sw.js").catch(() => {});

/* external links: in the APK open them in the system browser instead of inside the WebView */
document.addEventListener("click", e => {
  const a = e.target.closest && e.target.closest("a[href^='http']");
  if (!a || !IS_APP) return;
  e.preventDefault();
  const br = plugin("Browser");
  if (br && br.open) br.open({ url: a.href }).catch(() => { window.location.href = a.href; });
  else window.open(a.href, "_system");
});
