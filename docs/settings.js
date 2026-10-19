"use strict";
/* Pengaturan: edit config/bot-config.json, write-only API keys (Actions secrets, sealed box),
   live mode with confirmation, workflow buttons, notification toggles, bot ON/OFF and close position. */
(function () {
  const RAW_CFG = `https://raw.githubusercontent.com/${GH.REPO}/main/${GH.CONFIG_PATH}`;
  const PAT_URL = "https://github.com/settings/personal-access-tokens/new";
  const PROVIDERS = { nafnaf: { label: "NafNaf", models: "https://inference.nafnaf.ai/v1/models", secret: "NAFNAF_API_KEY" },
                      gonka: { label: "Gonka", models: "https://proxy.gonkabroker.com/v1/models", secret: "GONKA_API_KEY" },
                      arcee: { label: "Arcee", models: "https://api.arcee.ai/api/v1/models", secret: "ARCEE_API_KEY" } };
  const DEFAULT_CFG = {
    version: 3, enabled: true, venue: "both", mode: "paper", close_position_request: "", live_exit_request: "",
    strategy: { name: "ai_supertrend", timeframe: "1d" },
    ai: { enabled: true, providers: [{ name: "nafnaf", enabled: true, model: "qwen3.8-flash-next" }, { name: "gonka", enabled: true, model: "zai-org/GLM-5.3-Flash" }, { name: "arcee", enabled: true, model: "deepseek/deepseek-v4-flash-latest" }],
          temperature: 0.2, min_confidence: "low", extra_instruction: "" },
    risk: { max_exposure_pct: 100, vol_target_pct: 60, atr_guard_pct: 0, stoploss_enabled: true, stoploss_pct: 10, trailing_enabled: false, trailing_pct: 5,
            trailing_activate_pct: 3, roi_enabled: false, minimal_roi: { "0": 20, "1440": 10, "4320": 5 }, cooldown_enabled: true, cooldown_minutes: 1440,
            max_open_positions: 1, daily_loss_enabled: true, daily_max_loss_pct: 5, total_loss_enabled: true, total_max_loss_pct: 25 },
    paper: { start_idr: 1000000 }, live: { max_capital_idr: 500000, max_order_idr: 250000 }, backtest: { days: 365 },
    perps: { pairs: ["SOL", "BTC", "ETH"], timeframe: "4h", paper_start_usd: 250, max_leverage: 3, risk_per_trade_pct: 1, max_positions: 2,
             stop_atr_mult: 2, take_profit_r: 2, breakeven_at_r: 1, liq_buffer_mult: 2, entry_threshold: 0.25, allow_short: true, use_llm: true,
             daily_loss_enabled: true, daily_max_loss_pct: 3, kill_switch_enabled: true, total_max_loss_pct: 15, reset_request: "" },
  };
  const BASE_PAIRS = ["SOL", "BTC", "ETH"];
  const INT_PATHS = ["risk.max_open_positions", "perps.max_leverage", "perps.max_positions"];
  const root = document.getElementById("settingsRoot");
  let secrets = new Set(), models = {}, loadedOnce = false, ghUser = null;
  const esc = SB.esc;
  const clone = o => JSON.parse(JSON.stringify(o));
  const merge = (d, s) => { const o = clone(d); for (const k in s || {}) o[k] = (o[k] && typeof o[k] === "object" && !Array.isArray(o[k]) && s[k] && typeof s[k] === "object" && !Array.isArray(s[k])) ? merge(o[k], s[k]) : s[k]; return o; };
  const getP = (o, p) => p.split(".").reduce((a, k) => a == null ? a : a[k], o);
  const setP = (o, p, v) => { const ks = p.split("."); let a = o; ks.slice(0, -1).forEach(k => { a = a[k] = a[k] || {}; }); a[ks[ks.length - 1]] = v; };

  async function loadConfig() {
    try {
      if (GH.hasToken()) { const r = await GH.readConfig(); SB.cfg = merge(DEFAULT_CFG, r.cfg); }
      else SB.cfg = merge(DEFAULT_CFG, await SB.fetchJson(RAW_CFG));
    } catch (e) {
      if (!SB.cfg) SB.cfg = clone(DEFAULT_CFG);
      SB.cfgError = e.message;
    }
    if (GH.hasToken()) { try { secrets = await GH.secretNames(); } catch (e) { SB.secretErr = e.message; } }
  }

  /* ---------- small builders ---------- */
  const dis = () => GH.hasToken() ? "" : "disabled";
  const sw = (p, label, hint = "") => `<div class="fld"><label>${label}${hint ? `<small>${hint}</small>` : ""}</label><label class="switch"><input type="checkbox" data-p="${p}" ${getP(SB.cfg, p) ? "checked" : ""} ${dis()}><span></span></label></div>`;
  const nm = (p, label, unit, hint = "", step = "any", min = "", max = "") => `<div class="fld"><label>${label}${hint ? `<small>${hint}</small>` : ""}</label><span class="unit"><input class="in num${unit === "Rp" ? " wide" : ""}" type="number" inputmode="decimal" step="${step}" ${min !== "" ? `min="${min}"` : ""} ${max !== "" ? `max="${max}"` : ""} data-p="${p}" data-num="1" value="${esc(getP(SB.cfg, p))}" ${dis()}>${unit}</span></div>`;
  const sel = (p, label, opts, hint = "") => `<div class="fld"><label>${label}${hint ? `<small>${hint}</small>` : ""}</label><select class="in sel" data-p="${p}" ${dis()}>${opts.map(([v, t]) => `<option value="${v}" ${String(getP(SB.cfg, p)) === String(v) ? "selected" : ""}>${t}</option>`).join("")}</select></div>`;
  const secStatus = n => secrets.has(n) ? `<span class="st-ok">✓ tersimpan</span>` : `<span class="st-no">belum ada</span>`;
  const secField = (n, label) => `<div class="fld col"><div class="row"><span class="lb">${label}</span>${GH.hasToken() ? secStatus(n) : ""}</div>
      <div class="kvrow"><input class="in" type="password" autocomplete="off" placeholder="${secrets.has(n) ? "isi untuk mengganti" : "tempel di sini"}" data-secret="${n}" ${dis()}><button type="button" class="btn sm" data-savesecret="${n}" ${dis()}>Simpan</button></div></div>`;

  function render() {
    const c = SB.cfg, has = GH.hasToken(), live = c.mode === "live";
    const roi = Object.entries(c.risk.minimal_roi || {});
    root.innerHTML = `
    <section class="sec"><h2>Koneksi <b>GitHub</b></h2>
      ${has ? `<p class="small">🔑 Token tersimpan di HP ini${ghUser ? ` · repo <b>${esc(ghUser.full)}</b> ${ghUser.push === false ? '<span class="dn">(tanpa izin tulis!)</span>' : '<span class="up">OK</span>'}` : ""}.</p>
        ${SB.cfgError ? `<p class="err small">${esc(SB.cfgError)}</p>` : ""}${SB.secretErr ? `<p class="err small">Daftar secret: ${esc(SB.secretErr)}</p>` : ""}
        <button type="button" class="btn ghost sm" id="delTok">Hapus token</button>`
      : `<p class="small muted">Tanpa token, pengaturan hanya bisa dilihat (baca dari GitHub). Untuk mengubah, buat <b>fine-grained Personal Access Token</b>:</p>
        <ol class="small muted" style="padding-left:18px;margin:6px 0">
          <li>Buka <a href="${PAT_URL}" target="_blank" rel="noopener">github.com/settings/personal-access-tokens/new</a></li>
          <li>Repository access → <b>Only select repositories</b> → <b>${GH.REPO}</b></li>
          <li>Permissions (Repository): <b>Contents: Read and write</b>, <b>Actions: Read and write</b>, <b>Secrets: Read and write</b></li>
          <li>Generate, salin, tempel di bawah. Token hanya disimpan di HP ini.</li></ol>
        <div class="kvrow"><input class="in" id="tokIn" type="password" autocomplete="off" placeholder="github_pat_…"><button type="button" class="btn sm" id="saveTok">Simpan</button></div>`}
    </section>

    <section class="sec"><h2>Bot</h2>
      ${sw("enabled", "Bot aktif", "OFF = jadwal tetap jalan tapi bot tidak trading")}
      ${sel("strategy.name", "Strategi", [["ai_supertrend", "AI + Supertrend"], ["ema_rsi", "EMA cross + RSI"], ["bollinger", "Bollinger + RSI"]])}
      ${sel("strategy.timeframe", "Timeframe evaluasi", [["15m", "15 menit"], ["1h", "1 jam"], ["4h", "4 jam"], ["1d", "1 hari"]], "Bot dicek tiap ±15 mnt (jadwal GitHub bisa telat 5–30 mnt); strategi dievaluasi saat candle ini tutup. Stop/trailing dicek tiap pengecekan.")}
      ${nm("paper.start_idr", "Modal awal paper", "Rp", "Mengubahnya mereset dompet simulasi (jika belum ada trade)", "100000", "100000")}
      ${nm("backtest.days", "Periode backtest", "hari", "", "1", "30", "365")}
    </section>

    ${perpsHtml(c)}

    <section class="sec"><h2>AI</h2>
      ${sw("ai.enabled", "AI filter / penentu ukuran", "AI+Supertrend: AI memilih eksposur. Strategi lain: AI bisa menolak entry atau mengecilkan ukuran.")}
      <div id="provBox">${c.ai.providers.map((p, i) => provHtml(p, i, c.ai.providers.length)).join("")}</div>
      ${nm("ai.temperature", "Temperature", "", "0 = konsisten, makin tinggi makin variatif", "0.1", "0", "1.5")}
      ${sel("ai.min_confidence", "Keyakinan minimum untuk bertindak", [["low", "Rendah"], ["medium", "Sedang"], ["high", "Tinggi"]], "Di bawah ini bot memakai aturan strategi")}
      <div class="fld col"><label>Instruksi tambahan / persona AI <small>Maks 500 karakter. Tidak bisa melewati batas risiko.</small></label>
        <textarea class="in" maxlength="500" data-p="ai.extra_instruction" ${dis()}>${esc(c.ai.extra_instruction)}</textarea><small class="muted" id="xiCount"></small></div>
      <div class="row"><button type="button" class="btn ghost sm" id="testAi" ${dis()}>Tes AI</button><span class="small muted">jalankan workflow Cek AI</span></div>
      <div class="runst" id="aiRun" hidden></div>
    </section>

    <section class="sec"><h2>Risiko</h2>
      ${nm("risk.max_exposure_pct", "Eksposur maksimum", "%", "", "1", "0", "100")}
      ${nm("risk.vol_target_pct", "Target volatilitas", "%", "Eksposur maks = target ÷ volatilitas tahunan SOL", "5", "10", "300")}
      ${nm("risk.atr_guard_pct", "ATR guard", "%", "0 = mati. Jika ATR harian > nilai ini, eksposur maks dipotong separuh", "0.5", "0", "50")}
      ${sw("risk.stoploss_enabled", "Stop-loss")}${nm("risk.stoploss_pct", "Stop-loss dari harga masuk", "%", "", "0.5", "0.5", "50")}
      ${sw("risk.trailing_enabled", "Trailing stop")}${nm("risk.trailing_pct", "Jarak trailing dari puncak", "%", "", "0.5", "0.5", "50")}
      ${nm("risk.trailing_activate_pct", "Trailing aktif setelah untung", "%", "", "0.5", "0", "100")}
      ${sw("risk.roi_enabled", "Tabel minimal ROI", "Jual saat untung ≥ X% setelah N menit (gaya Freqtrade)")}
      <div id="roiBox">${roi.map(([k, v]) => roiRow(k, v)).join("")}</div>
      <button type="button" class="btn ghost sm" id="roiAdd" ${dis()}>+ baris ROI</button>
      ${sw("risk.cooldown_enabled", "Cooldown setelah rugi")}${nm("risk.cooldown_minutes", "Lama cooldown", "mnt", "", "15", "0", "10080")}
      ${sel("risk.max_open_positions", "Maks posisi terbuka", [["1", "1 (normal)"], ["0", "0 (tidak buka posisi baru)"]], "Satu pair (SOL/IDR), jadi maksimal 1")}
      ${sw("risk.daily_loss_enabled", "Batas rugi harian")}${nm("risk.daily_max_loss_pct", "Rugi harian maks", "%", "Kena → posisi ditutup, tak ada entry sampai besok (LIVE: bot mati + PAPER)", "0.5", "0.5", "50")}
      ${sw("risk.total_loss_enabled", "Kill switch rugi total")}${nm("risk.total_max_loss_pct", "Rugi total maks", "%", "Kena → posisi ditutup, bot dimatikan, mode PAPER", "1", "1", "90")}
      <p class="small muted">Rem pengaman yang selalu aktif: batas volatilitas, spot tanpa leverage, rebalance min 10%, cadangan Supertrend jika AI gagal.</p>
    </section>

    <section class="sec" id="liveSec"><h2>Mode <b>Live</b></h2>
      <div class="modecard ${live ? "live" : ""}"><div><b>${live ? "🔴 LIVE (uang sungguhan)" : "🧪 PAPER (simulasi)"}</b><div class="small muted">${live ? "Bot memakai saldo Indodax kamu" : "Default aman. Tidak ada order sungguhan."}</div></div>
        ${live ? `<button type="button" class="btn danger sm" id="stopLive2">STOP</button>` : `<button type="button" class="btn sm" id="goLive" ${dis()}>Aktifkan LIVE</button>`}</div>
      <ul class="req" id="liveReq"></ul>
      ${secField("INDODAX_API_KEY", "Indodax API key")}
      ${secField("INDODAX_SECRET", "Indodax secret key")}
      <p class="small muted">Buat API key di Indodax dengan izin <b>trade</b> saja — <b>tanpa izin withdraw</b>.</p>
      ${nm("live.max_capital_idr", "Modal maksimum live", "Rp", "Bot tidak memakai lebih dari ini", "50000", "50000")}
      ${nm("live.max_order_idr", "Maks per order", "Rp", "Order besar dipecah ke pengecekan berikutnya", "10000", "10000")}
    </section>

    <section class="sec"><h2>Notifikasi</h2>
      <div id="notifBox"></div>
    </section>

    <section class="sec"><h2>Aksi</h2>
      <button type="button" class="btn block" id="saveCfg" ${dis()}>💾 Simpan pengaturan</button>
      <button type="button" class="btn ghost block" id="runBot" ${dis()}>▶️ Jalankan bot sekarang (cek + evaluasi)</button>
      <div class="runst" id="botRun"></div>
      <p class="small muted">Mode trading: <b>${live ? "LIVE" : "PAPER"}</b>. Perubahan disimpan sebagai commit "chore: update bot config from app".</p>
    </section>`;
    bind();
    renderReq(); renderNotif();
    if (has) { GH.showLast("bot.yml", document.getElementById("botRun"), "Bot"); }
  }

  function perpsHtml(c) {
    const P = c.perps || DEFAULT_CFG.perps, extra = (P.pairs || []).filter(x => !BASE_PAIRS.includes(x));
    return `<section class="sec" id="perpsSec"><h2>Multi-agen · <b>Hyperliquid perps</b> (paper)</h2>
      ${sel("venue", "Venue", [["both", "Keduanya (Indodax + Hyperliquid)"], ["hyperliquid", "Hyperliquid perps saja"], ["indodax", "Indodax spot saja"]], "Hyperliquid selalu PAPER (uang simulasi). Indodax tetap seperti sebelumnya.")}
      <div class="fld col"><label>Pair yang diizinkan <small>Maks 8. Tambahan lain (mis. HYPE, DOGE) pisahkan dengan koma.</small></label>
        <div class="pairs">${BASE_PAIRS.map(x => `<label><input type="checkbox" data-pair="${x}" ${(P.pairs || []).includes(x) ? "checked" : ""} ${dis()}> ${x}</label>`).join("")}</div>
        <input class="in" id="pairExtra" placeholder="pair tambahan, mis. HYPE" value="${esc(extra.join(", "))}" ${dis()} style="margin-top:6px"></div>
      ${sel("perps.max_leverage", "Leverage maksimum", [1, 2, 3, 4, 5].map(x => [x, x + "x"]), "Batas keras 5x. Agen risiko memakai leverage serendah mungkin yang muat.")}
      ${nm("perps.risk_per_trade_pct", "Risiko maks per trade", "%", "Rugi di stop-loss = % ini dari ekuitas", "0.1", "0.1", "5")}
      ${sel("perps.max_positions", "Maks posisi bersamaan", [0, 1, 2, 3, 4, 5].map(x => [x, x === 0 ? "0 (tanpa entry baru)" : String(x)]))}
      ${sel("perps.timeframe", "Timeframe agen", [["15m", "15 menit"], ["1h", "1 jam"], ["4h", "4 jam"], ["1d", "1 hari"]], "Agen menilai saat candle ini tutup (1 panggilan AI untuk semua pair). Stop/TP dicek tiap ±15 mnt.")}
      ${nm("perps.paper_start_usd", "Modal paper", "$", "Berubah otomatis hanya jika belum ada trade; selain itu pakai Reset", "10", "20")}
      ${nm("perps.stop_atr_mult", "Stop = N × ATR", "×", "", "0.5", "0.5", "10")}
      ${nm("perps.take_profit_r", "Take-profit", "R", "Kelipatan jarak stop. 0 = tanpa TP", "0.5", "0", "20")}
      ${nm("perps.breakeven_at_r", "Stop ke impas setelah", "R", "0 = mati", "0.5", "0", "10")}
      ${nm("perps.liq_buffer_mult", "Buffer likuidasi", "× stop", "Harga likuidasi minimal sejauh N × jarak stop", "0.5", "1", "10")}
      ${nm("perps.entry_threshold", "Ambang skor koordinator", "", "0–1. Makin tinggi makin jarang masuk", "0.05", "0.05", "0.95")}
      ${sw("perps.allow_short", "Boleh short")}
      ${sw("perps.use_llm", "AI untuk 6 agen analis", "Satu panggilan AI per evaluasi untuk semua pair; gagal → aturan. Agen risiko selalu kode.")}
      ${sw("perps.daily_loss_enabled", "Batas rugi harian perps")}${nm("perps.daily_max_loss_pct", "Rugi harian maks", "%", "", "0.5", "0.5", "50")}
      ${sw("perps.kill_switch_enabled", "Kill switch perps")}${nm("perps.total_max_loss_pct", "Rugi total maks", "%", "Kena → semua posisi paper ditutup, tanpa entry baru sampai reset", "1", "1", "90")}
      <div class="row"><button type="button" class="btn ghost sm" id="resetPerps" ${dis()}>Reset paper perps</button><span class="small muted">Saldo kembali ke modal paper, riwayat perps dihapus</span></div>
      <p class="small muted">🔒 Tidak ada mode live untuk Hyperliquid di bot ini — hanya simulasi.</p>
    </section>`;
  }

  function provHtml(p, i, n) {
    const meta = PROVIDERS[p.name];
    return `<div class="prov" data-prov="${p.name}"><div class="row"><b>${i + 1}. ${meta.label}</b>
      <span class="ord"><button type="button" class="btn ghost" data-up="${i}" ${i === 0 ? "disabled" : ""} ${dis()}>↑</button><button type="button" class="btn ghost" data-down="${i}" ${i === n - 1 ? "disabled" : ""} ${dis()}>↓</button>
      <label class="switch"><input type="checkbox" data-pen="${p.name}" ${p.enabled ? "checked" : ""} ${dis()}><span></span></label></span></div>
      <div class="fld col"><label>Model</label><input class="in" list="ml-${p.name}" data-pmodel="${p.name}" value="${esc(p.model)}" ${dis()}><datalist id="ml-${p.name}">${(models[p.name] || []).map(m => `<option value="${esc(m)}">`).join("")}</datalist></div>
      ${secField(meta.secret, "API key " + meta.label)}</div>`;
  }
  const roiRow = (k, v) => `<div class="roi"><span class="unit"><input class="in" type="number" data-roik value="${esc(k)}" ${dis()}>mnt</span><span class="unit"><input class="in" type="number" step="0.5" data-roiv value="${esc(v)}" ${dis()}>%</span><button type="button" class="btn ghost sm" data-roidel ${dis()}>✕</button></div>`;

  function renderReq() {
    const ul = document.getElementById("liveReq"); if (!ul) return;
    const reqs = [[GH.hasToken(), "Token GitHub tersimpan"], [secrets.has("INDODAX_API_KEY"), "Secret INDODAX_API_KEY tersimpan"], [secrets.has("INDODAX_SECRET"), "Secret INDODAX_SECRET tersimpan"]];
    ul.innerHTML = reqs.map(([ok, t]) => `<li class="${ok ? "ok" : "no"}">${ok ? "✓" : "✗"} ${t}</li>`).join("") + `<li class="muted">Bot tetap memeriksa ulang saat jalan (key ada, config valid, kill switch) — kalau gagal, kembali ke PAPER.</li>`;
    return reqs.every(r => r[0]);
  }

  function renderNotif() {
    const st = SB.notify.settings();
    const box = document.getElementById("notifBox");
    const t = (k, label, hint = "") => `<div class="fld"><label>${label}${hint ? `<small>${hint}</small>` : ""}</label><label class="switch"><input type="checkbox" data-n="${k}" ${st[k] ? "checked" : ""}><span></span></label></div>`;
    box.innerHTML = t("master", "Notifikasi (semua)", SB.IS_APP ? "Dicek saat aplikasi dibuka & tiap ±15 mnt di latar belakang" : "Notifikasi HP hanya di aplikasi Android") +
      t("trade", "Trade baru (BELI/JUAL)") + t("risk", "Stop-loss / trailing / kill switch") + t("error", "Error bot / AI cadangan") + t("summary", "Ringkasan harian") +
      `<div class="row"><span class="small muted" id="permSt"></span><button type="button" class="btn ghost sm" id="testNotif">Tes notifikasi</button></div>
       <p class="small muted">Jika notifikasi latar belakang tidak muncul, matikan optimasi baterai untuk aplikasi ini (dontkillmyapp.com).</p>`;
    box.querySelectorAll("[data-n]").forEach(i => i.addEventListener("change", () => {
      const s = SB.notify.settings(); s[i.dataset.n] = i.checked; SB.notify.save(s);
      if (i.checked && SB.IS_APP) SB.notify.permission(true).then(showPerm);
    }));
    document.getElementById("testNotif").addEventListener("click", () => SB.notify.test().then(showPerm));
    showPerm();
  }
  async function showPerm() {
    const el = document.getElementById("permSt"); if (!el) return;
    const p = await SB.notify.permission(false);
    el.textContent = { granted: "Izin: diberikan ✓", denied: "Izin: ditolak", prompt: "Izin: belum diminta", "prompt-with-rationale": "Izin: belum diberikan", unavailable: "Izin: (bukan aplikasi)" }[p] || "Izin: " + p;
  }

  /* ---------- collect + save ---------- */
  function collect() {
    const c = clone(SB.cfg);
    root.querySelectorAll("[data-p]").forEach(i => {
      let v = i.type === "checkbox" ? i.checked : i.value;
      if (i.dataset.num) { v = parseFloat(String(v).replace(",", ".")); if (isNaN(v)) return; }
      if (INT_PATHS.includes(i.dataset.p)) v = parseInt(v, 10);
      setP(c, i.dataset.p, v);
    });
    c.ai.providers = [...root.querySelectorAll("[data-prov]")].map(d => ({ name: d.dataset.prov,
      enabled: d.querySelector("[data-pen]").checked, model: d.querySelector("[data-pmodel]").value.trim() || getDefaultModel(d.dataset.prov) }));
    const roi = {};
    root.querySelectorAll("#roiBox .roi").forEach(r => {
      const k = parseInt(r.querySelector("[data-roik]").value, 10), v = parseFloat(r.querySelector("[data-roiv]").value);
      if (!isNaN(k) && !isNaN(v) && k >= 0) roi[String(k)] = v;
    });
    if (Object.keys(roi).length) c.risk.minimal_roi = roi;
    const pairs = [...root.querySelectorAll("[data-pair]")].filter(i => i.checked).map(i => i.dataset.pair);
    const ex = root.querySelector("#pairExtra");
    if (ex) ex.value.split(/[\s,]+/).map(x => x.trim().toUpperCase()).filter(x => /^[A-Z0-9]{2,12}$/.test(x)).forEach(x => { if (!pairs.includes(x)) pairs.push(x); });
    c.perps = c.perps || clone(DEFAULT_CFG.perps);
    if (pairs.length) c.perps.pairs = pairs.slice(0, 8);
    c.version = 3;
    return c;
  }
  const getDefaultModel = n => (DEFAULT_CFG.ai.providers.find(p => p.name === n) || {}).model;

  async function writeCfg(c, msg, okMsg) {
    await GH.writeConfig(c, msg);
    SB.cfg = c; SB.toast(okMsg || "Pengaturan tersimpan ✓ (berlaku di pengecekan bot berikutnya)");
    SB.renderAll && SB.renderAll();
  }
  SB.writeCfg = writeCfg;

  async function save() {
    const b = document.getElementById("saveCfg"); b.disabled = true; b.textContent = "Menyimpan…";
    try {
      const c = collect();
      const fresh = await GH.readConfig().catch(() => null);
      if (fresh) { c.mode = fresh.cfg.mode || "paper"; c.close_position_request = fresh.cfg.close_position_request || ""; c.live_exit_request = fresh.cfg.live_exit_request || "";
        c.perps.reset_request = (fresh.cfg.perps && fresh.cfg.perps.reset_request) || ""; }
      await writeCfg(c, "chore: update bot config from app");
      render();
    } catch (e) { SB.toast("Gagal menyimpan: " + e.message, 6000); }
    finally { const b2 = document.getElementById("saveCfg"); if (b2) { b2.disabled = false; b2.textContent = "💾 Simpan pengaturan"; } }
  }

  /* ---------- live mode ---------- */
  function goLive() {
    if (!renderReq()) { SB.toast("Lengkapi dulu: token GitHub + secret INDODAX_API_KEY dan INDODAX_SECRET."); return; }
    const c = collect();
    const m = SB.modal(`<h3 class="dn">⚠️ Aktifkan mode LIVE?</h3>
      <p class="small">Bot akan membeli/menjual SOL dengan <b>uang sungguhan</b> di akun Indodax kamu.</p>
      <ul class="small"><li><b>Risiko kehilangan uang</b> — kripto bisa turun tajam, bot bisa salah, GitHub bisa telat atau gagal jalan.</li>
      <li><b>Bukan nasihat keuangan.</b> Tidak ada jaminan profit.</li>
      <li>Gunakan API key Indodax <b>tanpa izin withdraw</b>.</li>
      <li>Batas: modal maks ${SB.rp(c.live.max_capital_idr)}, per order ${SB.rp(c.live.max_order_idr)}, rugi harian ${c.risk.daily_loss_enabled ? c.risk.daily_max_loss_pct + "%" : "mati"}, rugi total ${c.risk.total_loss_enabled ? c.risk.total_max_loss_pct + "%" : "mati"}.</li></ul>
      <p class="small">Ketik <b>SAYA PAHAM</b> untuk melanjutkan:</p>
      <input class="in" id="confirmIn" autocomplete="off" autocapitalize="characters" placeholder="SAYA PAHAM">
      <div class="mbtns"><button type="button" class="btn ghost" id="mCancel">Batal</button><button type="button" class="btn danger" id="mOk" disabled>Aktifkan LIVE</button></div>`, "danger");
    const inp = m.el.querySelector("#confirmIn"), ok = m.el.querySelector("#mOk");
    inp.addEventListener("input", () => { ok.disabled = inp.value.trim() !== "SAYA PAHAM"; });
    m.el.querySelector("#mCancel").addEventListener("click", m.close);
    ok.addEventListener("click", async () => {
      ok.disabled = true; ok.textContent = "Menyimpan…";
      try {
        const fresh = await GH.readConfig();
        c.close_position_request = fresh.cfg.close_position_request || ""; c.live_exit_request = fresh.cfg.live_exit_request || "";
        c.mode = "live"; c.live_confirmed_at = new Date().toISOString();
        await writeCfg(c, "chore: enable LIVE mode from app", "Mode LIVE disimpan. Berlaku di pengecekan bot berikutnya.");
        m.close(); render();
      } catch (e) { SB.toast("Gagal: " + e.message, 6000); ok.disabled = false; ok.textContent = "Aktifkan LIVE"; }
    });
  }

  function stopLive() {
    if (!GH.hasToken()) { SB.toast("Butuh token GitHub di Pengaturan untuk menghentikan LIVE."); SB.show("settings"); return; }
    const m = SB.modal(`<h3>STOP / kembali ke Paper</h3>
      <p class="small">Mode diubah ke PAPER dan bot dijalankan sekarang. Mau sekalian menjual posisi SOL milik bot di Indodax?</p>
      <div class="mbtns" style="flex-direction:column"><button type="button" class="btn danger" id="sSell">Stop + jual posisi live</button>
      <button type="button" class="btn" id="sOnly">Stop saja (SOL tetap di Indodax)</button><button type="button" class="btn ghost" id="sCancel">Batal</button></div>`);
    const go = async sell => {
      m.el.querySelectorAll("button").forEach(b => b.disabled = true);
      try {
        const fresh = await GH.readConfig();
        const c = merge(DEFAULT_CFG, fresh.cfg);
        c.mode = "paper"; if (sell) c.live_exit_request = "stop-" + Date.now();
        await writeCfg(c, "chore: stop LIVE mode from app", "LIVE dihentikan → PAPER. Bot dijalankan…");
        m.close(); render();
        GH.runAndWatch("bot.yml", { force: "false" }, document.getElementById("botMsg"), "Bot");
      } catch (e) { SB.toast("Gagal: " + e.message, 6000); m.close(); }
    };
    m.el.querySelector("#sSell").addEventListener("click", () => go(true));
    m.el.querySelector("#sOnly").addEventListener("click", () => go(false));
    m.el.querySelector("#sCancel").addEventListener("click", m.close);
  }
  document.getElementById("stopLive").addEventListener("click", stopLive);

  /* ---------- bot switch + close position on Beranda ---------- */
  const botSw = document.getElementById("botEnabled");
  botSw.addEventListener("change", async () => {
    const want = botSw.checked;
    if (!GH.hasToken()) { botSw.checked = !want; SB.toast("Isi token GitHub dulu di Pengaturan untuk menyalakan/mematikan bot."); SB.show("settings"); return; }
    SB.pendingEnabled = true; botSw.disabled = true;
    try {
      const fresh = await GH.readConfig();
      const c = merge(DEFAULT_CFG, fresh.cfg); c.enabled = want;
      await writeCfg(c, `chore: bot ${want ? "ON" : "OFF"} from app`, want ? "Bot dinyalakan ✓" : "Bot dimatikan ✓ (posisi terbuka dipertahankan)");
      GH.runAndWatch("bot.yml", { force: "false" }, document.getElementById("botMsg"), "Bot").then(() => SB.reload());
    } catch (e) { botSw.checked = !want; SB.toast("Gagal: " + e.message, 6000); }
    finally { SB.pendingEnabled = false; botSw.disabled = false; }
  });
  document.getElementById("closePos").addEventListener("click", () => {
    if (!GH.hasToken()) { SB.toast("Isi token GitHub dulu di Pengaturan."); SB.show("settings"); return; }
    const mode = (SB.state && SB.state.mode) || "PAPER";
    const m = SB.modal(`<h3>Tutup posisi?</h3><p class="small">Bot akan menjual seluruh posisi SOL (${mode}) pada pengecekan yang dijalankan sekarang.${mode === "LIVE" ? " <b class='dn'>Ini order sungguhan.</b>" : ""}</p>
      <div class="mbtns"><button type="button" class="btn ghost" id="cCancel">Batal</button><button type="button" class="btn danger" id="cOk">Tutup posisi</button></div>`);
    m.el.querySelector("#cCancel").addEventListener("click", m.close);
    m.el.querySelector("#cOk").addEventListener("click", async () => {
      m.close();
      try {
        const fresh = await GH.readConfig();
        const c = merge(DEFAULT_CFG, fresh.cfg); c.close_position_request = "app-" + Date.now();
        await writeCfg(c, "chore: close position from app", "Permintaan tutup posisi dikirim…");
        GH.runAndWatch("bot.yml", { force: "false" }, document.getElementById("botMsg"), "Bot").then(() => SB.reload());
      } catch (e) { SB.toast("Gagal: " + e.message, 6000); }
    });
  });

  /* ---------- backtest button ---------- */
  document.getElementById("runBt").addEventListener("click", async () => {
    if (!GH.hasToken()) { SB.toast("Isi token GitHub dulu di Pengaturan."); SB.show("settings"); return; }
    const el = document.getElementById("btRun");
    try { await GH.runAndWatch("backtest.yml", {}, el, "Backtest"); setTimeout(() => SB.reload(), 4000); }
    catch (e) { el.hidden = false; el.textContent = "Gagal: " + e.message; }
  });

  /* ---------- bindings ---------- */
  function bind() {
    const q = s => root.querySelector(s);
    const st = q("#saveTok");
    if (st) st.addEventListener("click", async () => {
      const v = q("#tokIn").value.trim();
      if (!/^(github_pat_|ghp_)/.test(v)) { SB.toast("Token GitHub biasanya diawali github_pat_…"); return; }
      GH.setToken(v); st.disabled = true; st.textContent = "Cek…";
      try { ghUser = await GH.check(); SB.toast("Token tersimpan di HP ini ✓"); }
      catch (e) { SB.toast("Token disimpan, tapi cek gagal: " + e.message, 7000); }
      await refresh();
    });
    const dt = q("#delTok");
    if (dt) dt.addEventListener("click", async () => { GH.clearToken(); ghUser = null; secrets = new Set(); SB.toast("Token dihapus dari HP ini."); await refresh(); });
    root.querySelectorAll("[data-savesecret]").forEach(b => b.addEventListener("click", async () => {
      const n = b.dataset.savesecret, inp = root.querySelector(`[data-secret="${n}"]`), v = inp.value.trim();
      if (!v) { SB.toast("Kolom kosong."); return; }
      b.disabled = true; b.textContent = "…";
      try { await GH.setSecret(n, v); inp.value = ""; secrets.add(n); SB.toast(`${n} tersimpan (terenkripsi) ✓`); render(); }
      catch (e) { SB.toast("Gagal menyimpan secret: " + e.message, 7000); b.disabled = false; b.textContent = "Simpan"; }
    }));
    root.querySelectorAll("[data-up],[data-down]").forEach(b => b.addEventListener("click", () => {
      const c = collect(), i = +(b.dataset.up ?? b.dataset.down), j = b.dataset.up != null ? i - 1 : i + 1;
      [c.ai.providers[i], c.ai.providers[j]] = [c.ai.providers[j], c.ai.providers[i]];
      SB.cfg = c; render();
    }));
    const ra = q("#roiAdd"); if (ra) ra.addEventListener("click", () => { q("#roiBox").insertAdjacentHTML("beforeend", roiRow(2880, 3)); bindRoi(); });
    bindRoi();
    const xi = q('[data-p="ai.extra_instruction"]'), xc = q("#xiCount");
    const cnt = () => { xc.textContent = `${xi.value.length}/500`; }; xi.addEventListener("input", cnt); cnt();
    const sv = q("#saveCfg"); if (sv) sv.addEventListener("click", save);
    const rpb = q("#resetPerps"); if (rpb) rpb.addEventListener("click", () => {
      const m = SB.modal(`<h3>Reset paper perps?</h3><p class="small">Semua posisi & riwayat paper Hyperliquid dihapus, saldo kembali ke modal paper. Indodax tidak terpengaruh.</p>
        <div class="mbtns"><button type="button" class="btn ghost" id="rCancel">Batal</button><button type="button" class="btn danger" id="rOk">Reset</button></div>`);
      m.el.querySelector("#rCancel").addEventListener("click", m.close);
      m.el.querySelector("#rOk").addEventListener("click", async () => {
        m.close();
        try {
          const fresh = await GH.readConfig();
          const c = merge(DEFAULT_CFG, fresh.cfg); c.perps.reset_request = "reset-" + Date.now();
          await writeCfg(c, "chore: reset paper perps from app", "Reset dikirim, bot dijalankan…");
          GH.runAndWatch("bot.yml", { force: "true" }, document.getElementById("botRun"), "Bot").then(() => SB.reload());
        } catch (e) { SB.toast("Gagal: " + e.message, 6000); }
      });
    });
    const gl = q("#goLive"); if (gl) gl.addEventListener("click", goLive);
    const sl = q("#stopLive2"); if (sl) sl.addEventListener("click", stopLive);
    const ta = q("#testAi"); if (ta) ta.addEventListener("click", () => GH.runAndWatch("ai-check.yml", {}, q("#aiRun"), "Tes AI").catch(e => { q("#aiRun").hidden = false; q("#aiRun").textContent = "Gagal: " + e.message; }));
    const rb = q("#runBot"); if (rb) rb.addEventListener("click", () => GH.runAndWatch("bot.yml", { force: "true" }, q("#botRun"), "Bot").then(() => SB.reload()).catch(e => { q("#botRun").textContent = "Gagal: " + e.message; }));
  }
  function bindRoi() { root.querySelectorAll("[data-roidel]").forEach(b => { b.onclick = () => b.closest(".roi").remove(); }); }

  async function fetchModels() {
    await Promise.all(Object.entries(PROVIDERS).map(async ([n, p]) => {
      try { const j = await SB.fetchJson(p.models); models[n] = (j.data || []).map(m => m.id).filter(Boolean).slice(0, 80); } catch (e) {}
    }));
    root.querySelectorAll("datalist[id^=ml-]").forEach(dl => { const n = dl.id.slice(3); dl.innerHTML = (models[n] || []).map(m => `<option value="${esc(m)}">`).join(""); });
  }

  async function refresh() {
    await loadConfig();
    if (GH.hasToken() && !ghUser) { try { ghUser = await GH.check(); } catch (e) { SB.cfgError = e.message; } }
    render(); SB.renderAll && SB.renderAll();
  }

  SB.hooks.view.push(v => { if (v === "settings") { if (!loadedOnce) { loadedOnce = true; refresh().then(fetchModels); } else render(); } });
  window.addEventListener("DOMContentLoaded", () => { loadConfig().then(() => { SB.renderAll && SB.renderAll(); if (SB.view === "settings") { loadedOnce = true; render(); fetchModels(); } }); });
  SB.settings = { refresh, stopLive };
})();
