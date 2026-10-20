"use strict";
/* Agen tab: Hyperliquid perps PAPER — 6 analyst votes, coordinator, risk-agent decision/veto,
   open positions with stop / TP / liquidation, PnL per pair. Plus the multi-agent backtest card. */
(function () {
  const $ = id => document.getElementById(id);
  const esc = s => SB.esc(s);
  const RAW_BTP = `https://raw.githubusercontent.com/${GH.REPO}/main/docs/data/backtest_perps.json`;
  const usd = v => v == null || isNaN(v) ? "—" : (v < 0 ? "-" : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fp = v => { if (v == null || isNaN(v)) return "—"; const a = Math.abs(v); return a >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 0 }) : a >= 10 ? v.toFixed(2) : a >= 0.1 ? v.toFixed(4) : Number(v).toPrecision(4); };
  const pct = SB.pct;
  const LBL = { trend: "Tren", momentum: "Momentum", regime: "Volatilitas/Rezim", funding: "Funding & OI", sentiment: "Sentimen", liquidity: "Orderbook/Likuiditas" };
  const ORDER = ["trend", "momentum", "regime", "funding", "sentiment", "liquidity"];
  const DIR = { long: ["LONG", "up"], short: ["SHORT", "dn"], flat: ["FLAT", ""] };
  const EXIT = { stoploss: "stop-loss", take_profit: "take-profit", liquidation: "LIKUIDASI", breakeven_stop: "stop impas", "sinyal berbalik": "sinyal berbalik",
                 kill_total: "kill switch", kill_daily: "batas harian", "tutup manual dari aplikasi": "manual" };
  const dirTag = d => { const [t, c] = DIR[d] || [d, ""]; return `<span class="tag ${c}">${t}</span>`; };

  function renderMini(ps) {
    const el = $("agMini"); if (!el) return;
    if (!ps || !ps.summary) { el.hidden = true; return; }
    const sm = ps.summary, n = Object.keys((ps.book || {}).positions || {}).length;
    el.hidden = false;
    el.innerHTML = `<div class="row"><div><div class="label">🤖 Multi-agen · Hyperliquid (paper)</div>
      <div><b class="mid">${usd(sm.equity)}</b> <span class="pc ${sm.pnl_pct >= 0 ? "up" : "dn"}">${pct(sm.pnl_pct)}</span> <span class="muted small">· ${n} posisi · Hold ${sm.hold_equity ? pct(100 * (sm.hold_equity / sm.start - 1)) : "—"}</span></div></div>
      <button type="button" class="btn ghost sm" id="goAgents">Lihat agen ›</button></div>`;
    $("goAgents").addEventListener("click", () => SB.show("agents"));
  }

  function render() {
    const s = SB.state || {}, ps = s.perps, root = $("agentsRoot");
    renderMini(ps);
    if (!root) return;
    const venue = (SB.cfg && SB.cfg.venue) || s.venue || "both";
    if (!ps) {
      root.innerHTML = `<section class="card"><div class="label">Multi-agen · Hyperliquid</div>
        <p class="small muted">${venue === "indodax" ? "Venue di Pengaturan = Indodax saja. Pilih <b>Keduanya</b> atau <b>Hyperliquid</b> untuk menyalakan paper perps multi-agen." : "Belum ada data. Bot mengisi tab ini pada pengecekan berikutnya (±15 menit), atau tekan ▶️ Jalankan bot sekarang di Pengaturan."}</p></section>`;
      return;
    }
    const sm = ps.summary || {}, b = ps.book || {}, px = ps.prices || {};
    const llm = ps.llm || {};
    const tf = (SB.cfg && SB.cfg.perps && SB.cfg.perps.timeframe) || "4h";
    const last = Object.values(ps.latest || {}).reduce((a, d) => Math.max(a, d.ts || 0), 0);
    const notes = [];
    if (sm.kill) notes.push(sm.kill.until
      ? `⏸ Kill switch perps (${SB.wib(sm.kill.at)}, rugi ${sm.kill.loss_pct}%). Jeda tanpa entry baru sampai ${SB.wib(sm.kill.until)}, lalu lanjut otomatis.`
      : `⛔ Kill switch perps (${SB.wib(sm.kill.at)}, rugi ${sm.kill.loss_pct}%). Tidak ada entry baru — reset paper perps di Pengaturan.`);
    if (sm.daily_block) notes.push(`⏸ Batas rugi harian tercapai (${sm.daily_block}). Entry baru lagi besok (WIB).`);
    const vsHold = sm.hold_equity ? sm.equity - sm.hold_equity : null;
    let html = `
    <section class="card">
      <div class="row"><div class="label">Multi-agen · Hyperliquid perps</div><span class="badge paper">PAPER</span></div>
      <p class="small muted">6 agen analis → koordinator → <b>agen risiko</b> (kode, punya veto). Timeframe ${esc(tf)} · venue ${esc(venue)} · evaluasi terakhir ${last ? SB.wib(last) : "—"}</p>
      <div class="aiused ${/Cadangan|Aturan/.test(llm.source || "") ? "fb" : ""}">${esc(llm.source || "—")}</div>
      ${llm.error ? `<p class="err small">Catatan AI: ${esc(llm.error)}</p>` : ""}
      ${notes.map(n => `<p class="note bad small">${esc(n)}</p>`).join("")}
      <p class="small muted">Tidak ada order sungguhan: jalur live Hyperliquid sengaja tidak ada di bot ini.</p>
    </section>
    <section class="wallets">
      <div class="card w ai"><div class="label">🤖 Multi-agen</div><div class="mid">${usd(sm.equity)}</div><div class="pc ${sm.pnl_pct >= 0 ? "up" : "dn"}">${pct(sm.pnl_pct)}</div><div class="muted small">${sm.open || 0} posisi</div></div>
      <div class="card w hd"><div class="label">💎 Hold rata²</div><div class="mid">${usd(sm.hold_equity)}</div><div class="pc ${sm.hold_equity >= sm.start ? "up" : "dn"}">${sm.hold_equity ? pct(100 * (sm.hold_equity / sm.start - 1)) : "—"}</div><div class="muted small">beli & tahan</div></div>
      <div class="card w st"><div class="label">⚖️ vs Hold</div><div class="mid ${vsHold >= 0 ? "up" : "dn"}">${usd(vsHold)}</div><div class="pc muted">fee ${usd(sm.fees)}</div><div class="muted small">funding ${usd(-(sm.funding || 0))}</div></div>
    </section>
    <p class="muted small center">Modal paper ${usd(sm.start)} · saldo ${usd(sm.balance)} · margin terpakai ${usd(sm.used_margin)} · rugi hari ini ${sm.daily_loss_pct ?? 0}%</p>
    <section class="card"><div class="label">Kurva ekuitas perps vs Hold</div><div class="chartbox"><canvas id="agChart"></canvas></div></section>`;

    // open positions
    const pos = Object.values(b.positions || {});
    html += `<section class="card"><div class="label">Posisi paper terbuka</div>` + (pos.length ? pos.map(p => {
      const m = px[p.coin] || p.entry, sgn = p.side === "long" ? 1 : -1;
      const un = sgn * p.size * (m - p.entry) - p.funding, r = p.risk_usd ? un / p.risk_usd : null;
      return `<div class="pos"><div class="row"><b>${esc(p.coin)}</b>${dirTag(p.side)}<span class="chip">${p.leverage}x</span><span class="${un >= 0 ? "up" : "dn"}"><b>${usd(un)}</b>${r != null ? ` · ${r.toFixed(2)}R` : ""}</span></div>
        <div class="posgrid"><div><span>Ukuran</span>${Number(p.size).toPrecision(4)} ${esc(p.coin)} (${usd(p.notional)})</div><div><span>Margin</span>${usd(p.margin)}</div>
        <div><span>Masuk</span>${fp(p.entry)}</div><div><span>Harga (mid)</span>${fp(m)}</div>
        <div><span>Stop-loss</span><b class="dn">${fp(p.stop)}</b>${p.be_moved ? " (impas)" : ""}</div><div><span>Take-profit</span><b class="up">${fp(p.tp)}</b></div>
        <div><span>Likuidasi</span>${p.liq > 0 ? fp(p.liq) : "tidak ada (1x)"}</div><div><span>Risiko di stop</span>${usd(p.risk_usd)}</div>
        <div><span>Funding</span>${usd(-p.funding)}</div><div><span>Dibuka</span>${SB.wib(p.opened_at)}</div></div></div>`;
    }).join("") : `<p class="muted small">Tidak ada posisi terbuka.</p>`) + `</section>`;

    // per pair agent cards
    const latest = ps.latest || {};
    for (const c of Object.keys(latest)) {
      const d = latest[c], co = d.coordinator || {}, rk = d.risk || {};
      const pl = rk.plan;
      html += `<section class="card agent">
        <div class="row"><div><b class="mid">${esc(c)}</b> <span class="muted small">${fp(d.price)} · ${SB.wib(d.ts)}</span></div>${dirTag(co.direction)}</div>
        <div class="small muted">Koordinator: ${esc(co.reason || "")}</div>
        <div class="riskbox ${rk.approved ? "ok" : "veto"}">
          <b>${rk.approved ? "✅ Agen risiko: DISETUJUI" : "🛑 Agen risiko: VETO"}</b>
          ${rk.approved && pl ? `<div class="small">${dirTag(pl.side)} ${usd(pl.notional)} · ${pl.leverage}x · stop ${fp(pl.stop)} (${pl.stop_pct}%) · TP ${fp(pl.tp)} · liq ${pl.liq > 0 ? fp(pl.liq) : "—"} · risiko ${usd(pl.risk_usd)}</div>` : `<div class="small">${esc(rk.veto || "—")}</div>`}
          ${(rk.checks || []).length ? `<div class="small muted">${esc(rk.checks.join(" · "))}</div>` : ""}
          <div class="small muted">Aksi: ${esc(d.action || "—")}</div>
        </div>
        <ul class="votes">${ORDER.map(a => { const v = (d.votes || {})[a]; if (!v) return ""; return `<li><div class="row"><span>${LBL[a]} <span class="chip">${v.source === "llm" ? "AI" : "aturan"}</span></span>${dirTag(v.dir)}</div>
          <div class="bar"><i style="width:${Math.round(100 * v.conf)}%" class="${(DIR[v.dir] || [])[1] || ""}"></i></div><div class="small muted">${Math.round(100 * v.conf)}% · ${esc(v.reason)}</div></li>`; }).join("")}</ul>
      </section>`;
    }

    // pnl per pair
    const per = sm.per_pair || {};
    html += `<section class="card"><div class="label">PnL per pair</div><div class="tbl"><table><tr><th>Pair</th><th>Terealisasi</th><th>Berjalan</th><th>Trade</th><th>Win</th></tr>` +
      (Object.keys(per).length ? Object.entries(per).map(([c, r]) => `<tr><td>${esc(c)}</td><td class="${r.realized >= 0 ? "up" : "dn"}">${usd(r.realized)}</td><td class="${r.unrealized >= 0 ? "up" : "dn"}">${usd(r.unrealized)}</td><td>${r.trades}</td><td>${r.win_rate != null ? r.win_rate + "%" : "—"}</td></tr>`).join("")
        : `<tr><td colspan="5" class="muted">Belum ada trade.</td></tr>`) + `</table></div></section>`;

    // trades
    const tr = (b.trades || []).slice().reverse().slice(0, 40);
    html += `<section class="card"><div class="label">Trade perps selesai</div><ul class="hist">` + (tr.length ? tr.map(t => `<li class="hi"><div class="row"><b>${esc(t.coin)} ${dirTag(t.side)}</b><span class="tag ${t.pnl_usd >= 0 ? "up" : "dn"}">${usd(t.pnl_usd)}${t.r_multiple != null ? " · " + t.r_multiple + "R" : ""}</span></div>
      <div class="muted small">${SB.wib(t.closed_at)} · ${fp(t.entry)} → ${fp(t.exit)} · ${t.leverage}x · ${esc(EXIT[t.exit_reason] || t.exit_reason)} · fee ${usd(t.fees_usd)}</div></li>`).join("") : `<li class="muted">Belum ada trade.</li>`) + `</ul></section>`;

    // decision history
    const dec = (ps.decisions || []).slice().reverse().slice(0, 40);
    html += `<section class="card"><div class="label">Riwayat keputusan agen</div><ul class="hist">` + (dec.length ? dec.map(d => `<li class="hi"><div class="row"><b>${esc(d.coin)} · ${SB.wib(d.ts)}</b>${dirTag(d.dir)}</div>
      <div class="muted small">Skor ${d.score >= 0 ? "+" : ""}${Number(d.score).toFixed(2)} · ${d.approved ? "disetujui → " + esc(d.action) : "veto: " + esc(d.veto || "")}</div>
      <div class="small muted">${ORDER.map(a => d.votes && d.votes[a] ? `${LBL[a].split("/")[0]} ${d.votes[a][0][0].toUpperCase()}${Math.round(d.votes[a][1] * 100)}` : "").filter(Boolean).join(" · ")}</div></li>`).join("") : `<li class="muted">Belum ada.</li>`) + `</ul></section>`;
    root.innerHTML = html;
    drawChart(ps);
  }

  let chart;
  function drawChart(ps) {
    if (!window.Chart) { setTimeout(() => drawChart(ps), 300); return; }
    const pts = ps.equity || [], el = $("agChart"); if (!el) return;
    if (chart) chart.destroy();
    chart = new Chart(el, { type: "line",
      data: { labels: pts.map(p => new Date(p.t * 1000).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", day: "numeric", month: "short", hour: "2-digit" })),
        datasets: [{ label: "Multi-agen", data: pts.map(p => p.eq), borderColor: "#9945ff", backgroundColor: "#9945ff" },
                   { label: "Hold", data: pts.map(p => p.hold), borderColor: "#f5b942", backgroundColor: "#f5b942" }]
          .map(d => Object.assign(d, { pointRadius: pts.length > 40 ? 0 : 2, borderWidth: 2, tension: .2, spanGaps: true })) },
      options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#c8d2e3", boxWidth: 12 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${usd(c.parsed.y)}` } } },
        scales: { x: { ticks: { color: "#8b97ad", maxTicksLimit: 5 }, grid: { color: "#1b2336" } }, y: { ticks: { color: "#8b97ad", callback: v => "$" + Number(v).toFixed(Math.abs(v) >= 1000 ? 0 : 2) }, grid: { color: "#1b2336" } } } } });
  }

  /* ---------- multi-agent backtest card (Backtest tab) ---------- */
  let btChart;
  async function loadBt() {
    let bt = null;
    for (const u of [RAW_BTP, "data/backtest_perps.json"]) { try { bt = await SB.fetchJson(u); break; } catch (e) {} }
    const box = $("btpBox"); if (!box) return;
    if (!bt || !bt.stats) { box.innerHTML = `<p class="small muted">Belum ada hasil. Tekan "Jalankan backtest" (menjalankan Indodax + Hyperliquid).</p>`; return; }
    const st = bt.stats;
    const kv = [["Return multi-agen", pct(st.return_pct)], ["Return Hold rata²", pct(st.hold_return_pct)], ["Max drawdown", pct(st.max_drawdown_pct)],
      ["Drawdown Hold", pct(st.hold_max_drawdown_pct)], ["Jumlah trade", st.trades], ["Win rate", st.win_rate_pct != null ? st.win_rate_pct + "%" : "—"],
      ["Rata-rata R", st.avg_r ?? "—"], ["Profit factor", st.profit_factor ?? "—"], ["Ekuitas akhir", usd(st.final_equity)], ["Fee / funding", `${usd(st.fees_usd)} / ${usd(-st.funding_usd)}`],
      ["Kill switch", SB.killTxt ? SB.killTxt(st) : (st.killed_on ? "kena " + st.killed_on : "tidak")]];
    const per = bt.per_pair || {};
    box.innerHTML = `<p class="small muted">${esc(bt.pairs.join(", "))} · ${esc(bt.timeframe)} · ${bt.days} hari · ${esc(bt.mode)} · modal ${usd(bt.start_usd)} · dibuat ${SB.wib(bt.generated_at)}</p>
      <div class="kv">${kv.map(r => `<div><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("")}</div>
      <div class="chartbox" style="margin-top:10px"><canvas id="btpChart"></canvas></div>
      <div class="tbl"><table><tr><th>Pair</th><th>PnL</th><th>Trade</th><th>Win</th><th>Hold</th></tr>${Object.entries(per).map(([c, r]) => `<tr><td>${esc(c)}</td><td class="${r.pnl_usd >= 0 ? "up" : "dn"}">${usd(r.pnl_usd)}</td><td>${r.trades} (${r.long}L/${r.short}S)</td><td>${r.win_rate_pct != null ? r.win_rate_pct + "%" : "—"}</td><td>${pct(r.hold_return_pct)}</td></tr>`).join("")}</table></div>
      <p class="small muted">Veto agen risiko: ${esc(Object.entries(bt.risk_vetoes || {}).slice(0, 5).map(([k, v]) => `${k} (${v})`).join(" · ") || "—")}</p>
      <p class="small muted">${esc(bt.note || "")}</p>`;
    const draw = () => {
      if (!window.Chart) return setTimeout(draw, 300);
      if (btChart) btChart.destroy();
      const pts = bt.equity || [];
      btChart = new Chart($("btpChart"), { type: "line",
        data: { labels: pts.map(p => new Date(p.t * 1000).toLocaleDateString("id-ID", { day: "numeric", month: "short" })),
          datasets: [{ label: "Multi-agen", data: pts.map(p => p.bot), borderColor: "#9945ff", backgroundColor: "#9945ff" },
                     { label: "Hold", data: pts.map(p => p.hold), borderColor: "#f5b942", backgroundColor: "#f5b942" }].map(d => Object.assign(d, { pointRadius: 0, borderWidth: 2, tension: .2 })) },
        options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
          plugins: { legend: { labels: { color: "#c8d2e3", boxWidth: 12 } } },
          scales: { x: { ticks: { color: "#8b97ad", maxTicksLimit: 5 }, grid: { color: "#1b2336" } }, y: { ticks: { color: "#8b97ad", callback: v => "$" + Number(v).toFixed(Math.abs(v) >= 1000 ? 0 : 2) }, grid: { color: "#1b2336" } } } } });
    };
    draw();
  }

  SB.hooks.state.push(() => { render(); loadBt(); });
  SB.hooks.view.push(v => { if (v === "agents") render(); });
  SB.agents = { render };
})();
