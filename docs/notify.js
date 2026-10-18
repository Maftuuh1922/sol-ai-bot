"use strict";
/* In-app notifications: history list from state.events, Android local notifications for new events
   (checked on load/resume here, and every ~15 min in the background by runners/background.js). */
(function () {
  const SET_KEY = "solbot-notif-settings";
  const SEEN_KEY = "solbot-notif-seen";      // highest event seq already notified
  const READ_KEY = "solbot-notif-read";      // highest event seq the user has looked at
  const LABEL = "com.maftuuh.solaibot.check";
  const DEF = { master: true, trade: true, risk: true, error: true, summary: true };
  const TYPE_GROUP = { trade: "trade", risk: "risk", kill: "risk", error: "error", fallback: "error", summary: "summary" };
  const ICON = { agent: "🤖", trade: "💱", risk: "🛑", kill: "⛔", error: "⚠️", fallback: "⚠️", summary: "📊", info: "ℹ️" };

  const settings = () => { try { return Object.assign({}, DEF, JSON.parse(localStorage.getItem(SET_KEY) || "{}")); } catch (e) { return Object.assign({}, DEF); } };
  const getNum = k => { const v = parseInt(localStorage.getItem(k) || "", 10); return isNaN(v) ? null : v; };
  const LN = () => SB.plugin("LocalNotifications");
  const BR = () => SB.plugin("BackgroundRunner");
  const wanted = (e, st) => st.master && TYPE_GROUP[e.type] && st[TYPE_GROUP[e.type]];

  async function permission(request) {
    const ln = LN(); if (!ln) return "unavailable";
    try {
      let p = await ln.checkPermissions();
      if (p.display !== "granted" && request) p = await ln.requestPermissions();
      return p.display;
    } catch (e) { return "error"; }
  }

  async function ensureChannel() {
    const ln = LN(); if (!ln || ensureChannel.done) return;
    try { await ln.createChannel({ id: "bot", name: "Aktivitas bot", description: "Trade, stop-loss, kill switch, error, ringkasan", importance: 4 }); ensureChannel.done = true; } catch (e) {}
  }

  async function syncRunner(seen) {
    const br = BR(); if (!br) return seen;
    try {
      const r = await br.dispatchEvent({ label: LABEL, event: "sync", details: { seen: seen ?? -1, settings: settings() } });
      const rs = r && (r.seen ?? (r.value && r.value.seen));
      return typeof rs === "number" && rs > (seen ?? -1) ? rs : seen;
    } catch (e) { return seen; }
  }

  async function onState(s) {
    const evs = (s.events || []).filter(e => typeof e.seq === "number");
    const maxSeq = evs.reduce((a, e) => Math.max(a, e.seq), 0);
    let seen = getNum(SEEN_KEY);
    if (seen === null) seen = maxSeq;                     // first launch: don't replay history
    seen = await syncRunner(seen);                          // background runner may have notified some already
    const st = settings();
    const fresh = evs.filter(e => e.seq > seen && wanted(e, st));
    if (fresh.length && SB.IS_APP && (await permission(false)) === "granted") {
      await ensureChannel();
      const list = fresh.slice(-5).map(e => ({ id: e.seq, title: e.title, body: e.body, channelId: "bot", extra: { view: "notif" } }));
      if (fresh.length > 5) list.unshift({ id: maxSeq + 100000, title: `${fresh.length} kejadian baru dari bot`, body: "Buka tab Notif untuk detail.", channelId: "bot" });
      try { await LN().schedule({ notifications: list }); } catch (e) { console.warn(e); }
    }
    localStorage.setItem(SEEN_KEY, String(Math.max(seen, maxSeq)));
    await syncRunner(Math.max(seen, maxSeq));
    if (getNum(READ_KEY) === null) localStorage.setItem(READ_KEY, String(maxSeq));
    renderList(s);
  }

  function renderList(s) {
    const evs = (s.events || []).slice().reverse();
    const read = getNum(READ_KEY) ?? 0;
    const unread = evs.filter(e => e.seq > read).length;
    document.getElementById("unread").hidden = !unread;
    const ul = document.getElementById("notifList");
    ul.innerHTML = evs.length ? evs.map(e => `<li class="ev ${e.seq > read ? "unread" : ""}"><div class="ic">${ICON[e.type] || "•"}</div>
      <div><div class="tt">${SB.esc(e.title)}</div><div class="bd">${SB.esc(e.body)}</div>
      <div class="small muted">${SB.wib(e.at || e.ts)} · ${SB.esc(e.mode || "")}</div></div></li>`).join("")
      : `<li class="muted">Belum ada notifikasi. Kejadian bot (trade, stop, error, ringkasan harian) muncul di sini.</li>`;
  }

  function markRead() {
    const s = SB.state; if (!s) return;
    const maxSeq = (s.events || []).reduce((a, e) => Math.max(a, e.seq || 0), 0);
    localStorage.setItem(READ_KEY, String(maxSeq)); renderList(s);
  }
  document.getElementById("markRead").addEventListener("click", markRead);
  SB.hooks.view.push(v => { if (v === "notif") setTimeout(markRead, 1500); });
  SB.hooks.state.push(s => { onState(s).catch(console.error); });

  // tapping a notification opens the Notif tab
  window.addEventListener("DOMContentLoaded", () => {
    const ln = LN();
    if (ln) {
      try { ln.addListener("localNotificationActionPerformed", () => SB.show("notif")); } catch (e) {}
      if (!localStorage.getItem("solbot-notif-asked")) { localStorage.setItem("solbot-notif-asked", "1"); permission(true); }
    }
    const app = SB.plugin("App");
    if (app) try { app.addListener("resume", () => SB.reload()); } catch (e) {}
  });

  SB.notify = {
    settings, DEF,
    save(st) { localStorage.setItem(SET_KEY, JSON.stringify(st)); syncRunner(getNum(SEEN_KEY)); },
    permission,
    async test() {
      if (!SB.IS_APP) { SB.toast("Notifikasi HP hanya di aplikasi Android."); return; }
      const p = await permission(true);
      if (p !== "granted") { SB.toast("Izin notifikasi ditolak. Aktifkan di Pengaturan Android → Aplikasi → SOL AI Bot."); return; }
      await ensureChannel();
      await LN().schedule({ notifications: [{ id: 999999, title: "Tes notifikasi SOL AI Bot", body: "Notifikasi berfungsi ✅", channelId: "bot" }] });
    },
  };
})();
