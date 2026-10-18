"use strict";
/* Live SOL/IDR candlestick chart (lightweight-charts, bundled) from the Indodax public API.
   In the APK requests go through Capacitor native HTTP (no CORS). Polling pauses in background. */
(function () {
  const TF = { "15m": { api: "15", sec: 900, days: 2 }, "1h": { api: "60", sec: 3600, days: 10 },
               "4h": { api: "240", sec: 14400, days: 50 }, "1d": { api: "1D", sec: 86400, days: 300 } };
  const SHIFT = 7 * 3600;           // show WIB on the time axis
  const POLL_MS = 7000;
  let tf = localStorage.getItem("solbot-tf") || "1h";
  let chart, candles, stLine, markersApi, bars = [], timer = null, ref24 = null, ref24At = 0, failed = 0;
  const el = document.getElementById("mchart");

  function supertrend(b, period = 10, mult = 3) {
    const n = b.length, tr = [], atr = new Array(n).fill(null), up = new Array(n).fill(null), dn = new Array(n).fill(null), tdir = new Array(n).fill(1);
    for (let i = 0; i < n; i++) { const pc = i ? b[i - 1].c : b[i].c; tr.push(Math.max(b[i].h - b[i].l, Math.abs(b[i].h - pc), Math.abs(b[i].l - pc))); }
    for (let i = period - 1; i < n; i++) atr[i] = i === period - 1 ? tr.slice(0, period).reduce((a, x) => a + x, 0) / period : (atr[i - 1] * (period - 1) + tr[i]) / period;
    for (let i = 0; i < n; i++) {
      if (atr[i] == null) continue;
      const hl2 = (b[i].h + b[i].l) / 2; let bu = hl2 - mult * atr[i], bd = hl2 + mult * atr[i];
      if (i && up[i - 1] != null && b[i - 1].c > up[i - 1]) bu = Math.max(bu, up[i - 1]);
      if (i && dn[i - 1] != null && b[i - 1].c < dn[i - 1]) bd = Math.min(bd, dn[i - 1]);
      up[i] = bu; dn[i] = bd;
      let t = i ? tdir[i - 1] : 1;
      if (t === -1 && i && dn[i - 1] != null && b[i].c > dn[i - 1]) t = 1;
      else if (t === 1 && i && up[i - 1] != null && b[i].c < up[i - 1]) t = -1;
      tdir[i] = t;
    }
    return b.map((x, i) => atr[i] == null ? null : { time: x.t + SHIFT, value: tdir[i] === 1 ? up[i] : dn[i], color: tdir[i] === 1 ? "#14f195" : "#ff5c7a" }).filter(Boolean);
  }

  function placeholder(msg) {
    el.innerHTML = `<div class="ph">${msg}</div>`; chart = null;
  }

  function ensureChart() {
    if (chart) return true;
    const LW = window.LightweightCharts; if (!LW) return false;
    el.innerHTML = "";
    chart = LW.createChart(el, {
      autoSize: true, layout: { background: { color: "transparent" }, textColor: "#8b97ad", fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: "#1b2336" }, horzLines: { color: "#1b2336" } },
      rightPriceScale: { borderColor: "#222c42" }, timeScale: { borderColor: "#222c42", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 }, localization: { locale: "id-ID", priceFormatter: p => (p / 1e6).toFixed(3).replace(".", ",") + " jt" },
    });
    candles = chart.addSeries(LW.CandlestickSeries, { upColor: "#14f195", downColor: "#ff5c7a", borderVisible: false, wickUpColor: "#14f195", wickDownColor: "#ff5c7a" });
    stLine = chart.addSeries(LW.LineSeries, { color: "#f5b942", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    markersApi = LW.createSeriesMarkers ? LW.createSeriesMarkers(candles, []) : null;
    return true;
  }

  async function getJson(url) {
    const r = await SB.withTimeout(fetch(url, { cache: "no-store" }), 10000);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function botMarkers() {
    const s = SB.state; if (!s) return [];
    const sec = TF[tf].sec, out = [];
    const b = SB.book ? SB.book(s) : null;
    const orders = (b && b.orders) || [];
    for (const o of orders) out.push({ t: o.ts, side: o.side, price: o.price });
    if (!orders.length) for (const e of s.log || []) {
      if (/^(BUY|SELL)/.test(e.action || "")) out.push({ t: e.ran_at ? Date.parse(e.ran_at) / 1000 : null, side: e.action.slice(0, 4).trim(), price: e.close });
    }
    const first = bars.length ? bars[0].t : 0;
    return out.filter(m => m.t && m.t >= first).map(m => ({
      time: Math.floor(m.t / sec) * sec + SHIFT - (tf === "1d" ? 0 : 0),
      position: m.side === "BUY" ? "belowBar" : "aboveBar", color: m.side === "BUY" ? "#14f195" : "#ff5c7a",
      shape: m.side === "BUY" ? "arrowUp" : "arrowDown", text: m.side === "BUY" ? "BELI" : "JUAL",
    })).sort((a, b) => a.time - b.time);
  }

  async function loadCandles() {
    const c = TF[tf], now = Math.floor(Date.now() / 1000);
    document.getElementById("mInfo").textContent = "Memuat candle…";
    try {
      const rows = await getJson(`https://indodax.com/tradingview/history_v2?symbol=SOLIDR&tf=${c.api}&from=${now - c.days * 86400}&to=${now}`);
      bars = rows.map(r => ({ t: r.Time, o: +r.Open, h: +r.High, l: +r.Low, c: +r.Close })).sort((a, b) => a.t - b.t);
      if (!bars.length) throw new Error("kosong");
      if (!ensureChart()) return setTimeout(loadCandles, 300);
      candles.setData(bars.map(b => ({ time: b.t + SHIFT, open: b.o, high: b.h, low: b.l, close: b.c })));
      stLine.setData(supertrend(bars));
      if (markersApi) markersApi.setMarkers(botMarkers());
      chart.timeScale().fitContent();
      failed = 0;
      document.getElementById("mInfo").textContent = `${bars.length} candle ${tf} · Indodax`;
    } catch (e) {
      failed++;
      if (!SB.IS_APP) placeholder("Grafik live tersedia di aplikasi Android.<br>Browser memblokir API Indodax (CORS).");
      else placeholder("Gagal memuat candle Indodax. Cek internet lalu tekan ⟳.");
      document.getElementById("mInfo").textContent = "—";
    }
  }

  async function refreshRef() {
    if (Date.now() - ref24At < 5 * 60 * 1000 && ref24) return;
    const now = Math.floor(Date.now() / 1000);
    try {
      const rows = await getJson(`https://indodax.com/tradingview/history_v2?symbol=SOLIDR&tf=15&from=${now - 86400 - 1800}&to=${now - 86400 + 900}`);
      if (rows.length) { ref24 = +rows[rows.length - 1].Close; ref24At = Date.now(); }
    } catch (e) {}
  }

  async function poll() {
    try {
      const j = await getJson("https://indodax.com/api/ticker/solidr");
      const px = parseFloat(j.ticker.last);
      if (!(px > 0)) return;
      const first = !SB.livePx;
      SB.livePx = px;
      document.getElementById("price").textContent = SB.rp(px);
      document.getElementById("priceSrc").textContent = "· live";
      await refreshRef();
      const ch = document.getElementById("chg");
      if (ref24) { const p = (px / ref24 - 1) * 100; ch.textContent = SB.pct(p) + " 24j"; ch.className = "chg " + (p >= 0 ? "up" : "dn"); }
      if (chart && bars.length) {   // live-update the last candle
        const sec = TF[tf].sec, t = Math.floor(j.ticker.server_time / sec) * sec;
        let last = bars[bars.length - 1];
        if (t > last.t) { last = { t, o: last.c, h: px, l: px, c: px }; bars.push(last); }
        else { last.c = px; last.h = Math.max(last.h, px); last.l = Math.min(last.l, px); }
        candles.update({ time: last.t + SHIFT, open: last.o, high: last.h, low: last.l, close: last.c });
      }
      if (first || Date.now() - (poll._r || 0) > 30000) { poll._r = Date.now(); SB.renderAll(); }
    } catch (e) { /* browser CORS or offline: keep bot price */ }
  }

  function active() { return document.visibilityState === "visible" && SB.view === "home"; }
  function start() { if (!timer && active()) { poll(); timer = setInterval(() => active() ? poll() : stop(), POLL_MS); } }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  for (const b of document.querySelectorAll("#tfs button")) {
    b.classList.toggle("on", b.dataset.tf === tf);
    b.addEventListener("click", () => {
      tf = b.dataset.tf; localStorage.setItem("solbot-tf", tf);
      for (const x of document.querySelectorAll("#tfs button")) x.classList.toggle("on", x === b);
      loadCandles();
    });
  }
  document.addEventListener("visibilitychange", () => { if (active()) { start(); if (failed || !bars.length) loadCandles(); } else stop(); });
  SB.hooks.view.push(v => { if (v === "home") start(); else stop(); });
  SB.hooks.state.push(() => { if (markersApi && bars.length) markersApi.setMarkers(botMarkers()); });
  document.getElementById("refresh").addEventListener("click", loadCandles);
  window.addEventListener("DOMContentLoaded", () => { loadCandles(); start(); });
  SB.market = { reload: loadCandles };
})();
