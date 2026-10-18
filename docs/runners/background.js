/* Background runner (Capacitor Background Runner, Android WorkManager ~every 15 min).
   Fetches the small events feed the bot publishes and shows a local notification for new events.
   No DOM, no localStorage: settings + last seen seq live in CapacitorKV (synced from the app). */
var FEED = "https://raw.githubusercontent.com/Maftuuh1922/sol-ai-bot/main/docs/data/events.json";
var GROUP = { trade: "trade", risk: "risk", kill: "risk", error: "error", fallback: "error", summary: "summary" };

function kvGet(k) { try { var r = CapacitorKV.get(k); var v = r && typeof r === "object" ? r.value : r; return v == null ? null : v; } catch (e) { return null; } }
function kvNum(k) { var v = parseInt(kvGet(k) || "", 10); return isNaN(v) ? null : v; }
function settings() {
  var d = { master: true, trade: true, risk: true, error: true, summary: true };
  try { var s = JSON.parse(kvGet("settings") || "{}"); for (var k in s) d[k] = s[k]; } catch (e) {}
  return d;
}

addEventListener("sync", function (resolve, reject, args) {
  try {
    var a = args || {};
    var seen = kvNum("seen");
    if (typeof a.seen === "number" && a.seen >= 0 && (seen === null || a.seen > seen)) { seen = a.seen; CapacitorKV.set("seen", String(seen)); }
    if (a.settings) CapacitorKV.set("settings", JSON.stringify(a.settings));
    resolve({ seen: seen === null ? -1 : seen });
  } catch (e) { reject(e); }
});

addEventListener("checkState", async function (resolve, reject) {
  try {
    var st = settings();
    var res = await fetch(FEED + "?t=" + Date.now());
    var feed = JSON.parse(await res.text());
    var evs = (feed.events || []).filter(function (e) { return typeof e.seq === "number"; });
    var max = evs.reduce(function (a, e) { return Math.max(a, e.seq); }, 0);
    var seen = kvNum("seen");
    if (seen === null) { CapacitorKV.set("seen", String(max)); resolve(); return; }
    var fresh = evs.filter(function (e) { return e.seq > seen && st.master && GROUP[e.type] && st[GROUP[e.type]]; });
    if (fresh.length) {
      var list = fresh.slice(-4).map(function (e) { return { id: e.seq, title: e.title, body: e.body, channelId: "bot", smallIcon: "ic_stat_notify" }; });
      if (fresh.length > 4) list.unshift({ id: max + 100000, title: fresh.length + " kejadian baru dari bot", body: "Buka aplikasi untuk detail.", channelId: "bot", smallIcon: "ic_stat_notify" });
      CapacitorNotifications.schedule(list);
    }
    if (max > seen) CapacitorKV.set("seen", String(max));
    resolve();
  } catch (e) { console.error("solbot runner: " + e); reject(e); }
});
