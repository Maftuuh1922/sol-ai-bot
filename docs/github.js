"use strict";
/* Minimal GitHub REST client for the app. The fine-grained token lives only on this device (localStorage). */
(function () {
  const API = "https://api.github.com";
  const REPO = "Maftuuh1922/sol-ai-bot";
  const TK = "solbot-gh-token";
  const CONFIG_PATH = "config/bot-config.json";

  const token = () => localStorage.getItem(TK) || "";
  function b64encodeUtf8(str) { const b = new TextEncoder().encode(str); let s = ""; for (const x of b) s += String.fromCharCode(x); return btoa(s); }
  function b64decodeUtf8(b64) { const s = atob(b64.replace(/\s/g, "")); const a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return new TextDecoder().decode(a); }

  async function gh(method, path, body) {
    const t = token(); if (!t) throw new Error("Token GitHub belum diisi");
    const r = await SB.withTimeout(fetch(API + path, {
      method, headers: { "Authorization": "Bearer " + t, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                         "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined, cache: "no-store",
    }), 20000);
    const txt = await r.text();
    let j = null; try { j = txt ? JSON.parse(txt) : null; } catch (e) {}
    if (!r.ok) {
      const msg = (j && j.message) || txt.slice(0, 160) || ("HTTP " + r.status);
      const hint = r.status === 401 ? " (token salah/kedaluwarsa)" : r.status === 403 || r.status === 404 ? " (izin token kurang atau repo bukan milik token)" : "";
      const e = new Error(`GitHub ${r.status}: ${msg}${hint}`); e.status = r.status; throw e;
    }
    return j;
  }

  const GH = {
    REPO, CONFIG_PATH,
    token, hasToken: () => !!token(),
    setToken: t => localStorage.setItem(TK, t.trim()),
    clearToken: () => localStorage.removeItem(TK),
    async check() {
      const repo = await gh("GET", `/repos/${REPO}`);
      return { full: repo.full_name, push: repo.permissions ? !!repo.permissions.push : null };
    },
    async readConfig() {
      const j = await gh("GET", `/repos/${REPO}/contents/${CONFIG_PATH}?ref=main&t=${Date.now()}`);
      return { cfg: JSON.parse(b64decodeUtf8(j.content)), sha: j.sha };
    },
    async writeConfig(cfg, message = "chore: update bot config from app") {
      let sha;
      try { sha = (await gh("GET", `/repos/${REPO}/contents/${CONFIG_PATH}?ref=main&t=${Date.now()}`)).sha; } catch (e) { if (e.status !== 404) throw e; }
      const content = b64encodeUtf8(JSON.stringify(cfg, null, 2) + "\n");
      return gh("PUT", `/repos/${REPO}/contents/${CONFIG_PATH}`, { message, content, sha, branch: "main" });
    },
    async secretNames() {
      const j = await gh("GET", `/repos/${REPO}/actions/secrets?per_page=100`);
      return new Set((j.secrets || []).map(s => s.name));
    },
    async setSecret(name, value) {
      if (!window.sealSecret) throw new Error("modul enkripsi belum termuat");
      const pk = await gh("GET", `/repos/${REPO}/actions/secrets/public-key`);
      const encrypted_value = window.sealSecret(value, pk.key);
      await gh("PUT", `/repos/${REPO}/actions/secrets/${name}`, { encrypted_value, key_id: pk.key_id });
    },
    async dispatch(workflow, inputs) {
      await gh("POST", `/repos/${REPO}/actions/workflows/${workflow}/dispatches`, { ref: "main", inputs: inputs || {} });
    },
    async lastRun(workflow) {
      const j = await gh("GET", `/repos/${REPO}/actions/workflows/${workflow}/runs?per_page=1&t=${Date.now()}`);
      return (j.workflow_runs || [])[0] || null;
    },
  };

  /** Dispatch a workflow and keep `el` updated with the newest run's status. */
  GH.runAndWatch = async function (workflow, inputs, el, label) {
    el.hidden = false;
    el.innerHTML = `⏳ Mengirim ${SB.esc(label)}…`;
    const before = await GH.lastRun(workflow).catch(() => null);
    await GH.dispatch(workflow, inputs);
    const t0 = Date.now();
    let run = null;
    while (Date.now() - t0 < 6 * 60 * 1000) {
      await new Promise(r => setTimeout(r, 5000));
      run = await GH.lastRun(workflow).catch(() => null);
      if (!run || (before && run.id === before.id)) { el.innerHTML = `⏳ ${SB.esc(label)}: menunggu GitHub memulai…`; continue; }
      const st = run.status === "completed" ? (run.conclusion === "success" ? "✅ selesai" : "❌ " + run.conclusion) : "⏳ " + (run.status === "queued" ? "antre" : "berjalan");
      el.innerHTML = `${st} · <a href="${run.html_url}" target="_blank" rel="noopener">lihat run #${run.run_number}</a>`;
      if (run.status === "completed") break;
    }
    return run;
  };

  GH.showLast = async function (workflow, el, label) {
    try {
      const run = await GH.lastRun(workflow);
      if (!run) { el.textContent = `${label}: belum pernah dijalankan.`; return; }
      const st = run.status === "completed" ? (run.conclusion === "success" ? "✅" : "❌ " + run.conclusion) : "⏳ " + run.status;
      el.innerHTML = `${SB.esc(label)} terakhir: ${st} · ${SB.wib(run.created_at)} · <a href="${run.html_url}" target="_blank" rel="noopener">#${run.run_number}</a>`;
    } catch (e) { el.textContent = `${label}: ${e.message}`; }
  };
  window.GH = GH;
})();
