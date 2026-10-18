"use strict";
const $ = id => document.getElementById(id);
const REPO = "Maftuuh1922/sol-ai-bot";
const RAW_STATE = `https://raw.githubusercontent.com/${REPO}/main/docs/data/state.json`;
const APK_URL = `https://github.com/${REPO}/releases/latest/download/sol-ai-bot.apk`;
const IS_APP = !!(window.Capacitor && (typeof window.Capacitor.isNativePlatform !== "function" || window.Capacitor.isNativePlatform()));
// Served from the repo itself (GitHub Pages) -> relative file is the freshest; otherwise (APK, local file, other host) use raw GitHub.
const ON_PAGES = /\.github\.io$/i.test(location.hostname);
const CACHE_KEY = "solbot-state-v1";
const START = 1000000;
const rp = v => v == null || isNaN(v) ? "—" : "Rp" + Math.round(v).toLocaleString("id-ID");
const pct = v => (v >= 0 ? "+" : "") + v.toFixed(2).replace(".", ",") + "%";
const wib = iso => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("id-ID", { timeZone: "Asia/Jakarta", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) + " WIB";
};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function setPc(el, eq, start) {
  if (eq == null) { el.textContent = "—"; return; }
  const p = (eq / start - 1) * 100;
  el.textContent = pct(p);
  el.className = "pc " + (p >= 0 ? "up" : "dn");
}

function walletEq(w, px) { return w ? (w.idr || 0) + (w.sol || 0) * px : null; }

function render(s, px, live) {
  const start = s.start_idr || START;
  const mode = (s.mode || "PAPER").toUpperCase();
  $("mode").textContent = mode;
  $("mode").className = "badge " + mode.toLowerCase();
  $("updated").textContent = wib(s.updated_at);
  $("price").textContent = rp(px);
  $("priceSrc").textContent = live ? "· live" : "· dari bot";

  const eAi = walletEq(s.ai, px), eSt = walletEq(s.bench, px), eHd = s.hold && s.hold.sol ? walletEq(s.hold, px) : null;
  $("eqAi").textContent = rp(eAi); setPc($("pcAi"), eAi, start);
  $("eqSt").textContent = rp(eSt); setPc($("pcSt"), eSt, start);
  $("eqHd").textContent = rp(eHd); setPc($("pcHd"), eHd, start);
  if (s.ai && px) $("expAi").textContent = "Eksposur " + Math.round(100 * (s.ai.sol * px) / (eAi || 1)) + "%";
  if (s.bench && px) $("expSt").textContent = "Eksposur " + Math.round(100 * (s.bench.sol * px) / (eSt || 1)) + "%";

  const log = s.log || [];
  const d = log[log.length - 1];
  $("decErr").hidden = true;
  if (d) {
    $("decDate").textContent = "Candle " + d.date;
    $("decTarget").textContent = Math.round(d.final * 100) + "%";
    const conf = { low: "Rendah", medium: "Sedang", high: "Tinggi", fallback: "Cadangan" }[d.confidence] || d.confidence || "—";
    $("decConf").textContent = conf;
    $("decAct").textContent = d.action || "—";
    $("decReason").textContent = d.reason || "—";
    const meta = [];
    if (d.provider) meta.push("Sumber: " + d.provider);
    if (d.ai_target != null && Math.abs(d.ai_target - d.final) > 0.001) meta.push(`Usulan AI ${Math.round(d.ai_target * 100)}% → dibatasi ${Math.round(d.final * 100)}% (batas volatilitas ${Math.round(d.cap * 100)}%)`);
    else if (d.cap != null) meta.push(`Batas volatilitas ${Math.round(d.cap * 100)}%`);
    if (d.ran_at) meta.push("Diproses " + wib(d.ran_at));
    $("decMeta").textContent = meta.join(" · ");
    if (d.error) { $("decErr").hidden = false; $("decErr").textContent = "Catatan error: " + d.error; }
  }

  const m = s.market || {};
  const up = (m.supertrend || (d && d.supertrend)) === "UP";
  if (m.supertrend || d) {
    $("stDir").textContent = up ? "▲ NAIK (UP)" : "▼ TURUN (DOWN)";
    $("stDir").className = "pill " + (up ? "up" : "dn");
  }
  $("stLine").textContent = rp(m.supertrend_line_idr);
  const rows = [
    ["Hari sejak ganti tren", m.days_since_trend_flip],
    ["RSI 14", m.rsi14],
    ["EMA 50", rp(m.ema50_idr)], ["EMA 200", rp(m.ema200_idr)],
    ["ATR 14", m.atr14_pct != null ? m.atr14_pct + "%" : null],
    ["Volatilitas 30h (thn)", m.realized_vol_30d_annual_pct != null ? m.realized_vol_30d_annual_pct + "%" : null],
    ["Return 7h", m.ret_7d_pct != null ? pct(m.ret_7d_pct) : null],
    ["Return 30h", m.ret_30d_pct != null ? pct(m.ret_30d_pct) : null],
    ["Return 90h", m.ret_90d_pct != null ? pct(m.ret_90d_pct) : null],
    ["Dari puncak 365h", m.drawdown_from_365d_high_pct != null ? pct(m.drawdown_from_365d_high_pct) : null],
  ].filter(r => r[1] != null && r[1] !== "—");
  $("mkt").innerHTML = rows.map(r => `<div><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("");

  if (log.length) {
    $("hist").innerHTML = log.slice().reverse().map(e => `<li class="hi">
      <div class="row"><b>${esc(e.date)}</b><span class="tag ${/^BUY/.test(e.action) ? "up" : /^SELL/.test(e.action) ? "dn" : ""}">${esc(e.action || "HOLD")}</span></div>
      <div class="muted small">Close ${rp(e.close)} · Target ${Math.round(e.final * 100)}% · ST <span class="${e.supertrend === "UP" ? "up" : "dn"}">${esc(e.supertrend)}</span>${e.confidence === "fallback" ? " · cadangan" : ""}</div>
      ${e.reason ? `<div class="hr">${esc(e.reason)}</div>` : ""}</li>`).join("");
  }

  drawChart(s.equity || [], start, live ? { px, ai: eAi, bench: eSt, hold: eHd } : null);
}

let chart;
function drawChart(pts, start, livePt) {
  if (!window.Chart) { setTimeout(() => drawChart(pts, start, livePt), 300); return; }
  const data = pts.slice();
  const labels = [start ? "Mulai" : ""].concat(data.map(p => p.date));
  if (livePt && livePt.ai != null) {
    labels.push("Sekarang");
    data.push(livePt);
  }
  const ser = k => [start].concat(data.map(p => p[k] ?? null));
  $("chartNote").textContent = pts.length < 2 ? "Kurva akan terisi otomatis seiring bot berjalan tiap hari." : "";
  const ds = [
    { label: "AI", data: ser("ai"), borderColor: "#9945ff", backgroundColor: "#9945ff" },
    { label: "Supertrend", data: ser("bench"), borderColor: "#5a8cf0", backgroundColor: "#5a8cf0" },
    { label: "Hold", data: ser("hold"), borderColor: "#f5b942", backgroundColor: "#f5b942" },
  ].map(d => Object.assign(d, { tension: .25, pointRadius: data.length > 40 ? 0 : 3, borderWidth: 2, spanGaps: true }));
  const vals = ds.flatMap(d => d.data).filter(v => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = Math.max((hi - lo) * 0.1, start * 0.01);
  if (chart) chart.destroy();
  chart = new Chart($("chart"), {
    type: "line", data: { labels, datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#c8d2e3", boxWidth: 12 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${rp(c.parsed.y)}` } } },
      scales: {
        x: { ticks: { color: "#8b97ad", maxTicksLimit: 6 }, grid: { color: "#1b2336" } },
        y: { suggestedMin: lo - pad, suggestedMax: hi + pad,
             ticks: { color: "#8b97ad", maxTicksLimit: 6, callback: v => (v / 1e6).toFixed(hi - lo < start * 0.05 ? 3 : 2).replace(".", ",") + " jt" }, grid: { color: "#1b2336" } },
      },
    },
  });
}

function withTimeout(p, ms) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]);
}

async function livePrice() {
  try {
    const r = await withTimeout(fetch("https://indodax.com/api/ticker/solidr"), 6000);
    const j = await r.json();
    const v = parseFloat(j.ticker.last);
    return isFinite(v) && v > 0 ? v : null;
  } catch (e) { return null; }   // di browser biasanya diblokir CORS -> pakai harga dari bot; di APK lewat HTTP native
}

async function fetchJson(url) {
  const r = await withTimeout(fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" }), 12000);
  if (!r.ok) throw new Error("HTTP " + r.status);
  const j = await r.json();
  if (!j || typeof j !== "object") throw new Error("data kosong");
  return j;
}

async function loadState() {
  const urls = ON_PAGES ? ["data/state.json", RAW_STATE] : [RAW_STATE, "data/state.json"];
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch (e) {}
  for (const u of urls) {
    // the bundled copy inside the APK is only a last resort, after the last good download
    if (IS_APP && u === "data/state.json" && cached) return { s: cached, src: "cache" };
    try {
      const s = await fetchJson(u);
      try { localStorage.setItem(CACHE_KEY, JSON.stringify(s)); } catch (e) {}
      return { s, src: u === RAW_STATE ? "github" : "local" };
    } catch (e) { /* try next */ }
  }
  if (cached) return { s: cached, src: "cache" };
  throw new Error("Gagal memuat data bot");
}

let lastLoad = 0, loading = false;
async function main() {
  if (loading) return;
  loading = true; lastLoad = Date.now();
  $("refresh").classList.add("spin");
  try {
    let res;
    try { res = await loadState(); }
    catch (e) { $("decReason").textContent = "Gagal memuat data bot. Cek koneksi internet lalu tekan ⟳."; return; }
    const s = res.s;
    $("offline").hidden = res.src !== "cache";
    const lastClose = s.log && s.log.length ? s.log[s.log.length - 1].close : (s.market || {}).close_idr;
    render(s, lastClose, false);
    const lp = await livePrice();
    if (lp) render(s, lp, true);
  } finally {
    loading = false;
    $("refresh").classList.remove("spin");
  }
}

$("refresh").addEventListener("click", main);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - lastLoad > 60000) main();
});
if (!IS_APP) {
  const a = $("apk"); if (a) { a.href = APK_URL; a.hidden = false; }
}
main();
if (!IS_APP && "serviceWorker" in navigator && /^https?:$/.test(location.protocol)) navigator.serviceWorker.register("sw.js").catch(() => {});
