"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { tab: "live", accounts: [], streamers: [], clipStatus: "open", clipStreamer: "", clipSig: {} };

const CAT = { funny: "😂 Lustig", hype: "🔥 Hype", shock: "😳 Schock", fail: "💀 Fail", action: "💥 Action" };
const STATUS = { live: "LIVE", paused: "LIVE · kein GTA", live_other: "LIVE · kein GTA", offline: "Offline",
  checking: "Prüfe …", error: "Fehler", disabled: "Deaktiviert" };
const CLIP_STATUS = { queued: "In Warteschlange", processing: "Wird verarbeitet", ready: "Bereit", failed: "Fehler",
  uploaded: "Hochgeladen", discarded: "Verworfen" };

async function api(method, url, body) {
  const res = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), 3500);
}

const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.floor(s / 60)} min`;
  if (s < 86400) return `vor ${Math.floor(s / 3600)} h`;
  return new Date(ts * 1000).toLocaleDateString("de-DE");
};
const dur = (s) => (s < 3600 ? `${Math.floor(s / 60)} min`
  : `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} h`);

/* ------------------------------------------------------------------ Tabs */
$$("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(tab) {
  state.tab = tab;
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${tab}`));
  location.hash = tab;
  refresh();
}

async function refresh() {
  try {
    if (state.tab === "live") await loadLive();
    if (state.tab === "clips") await loadClips();
    if (state.tab === "streamers") await loadStreamers();
    if (state.tab === "accounts") await loadAccounts();
  } catch (e) { console.error(e); }
}

/* ------------------------------------------------------------------ Live */
async function loadLive() {
  const st = await api("GET", "/api/status");
  const q = Object.keys(st.processing).length;
  $("#queue").textContent = q || st.queue ? `⚙ ${q} in Arbeit · ${st.queue} wartend` : "";
  const grid = $("#live-grid");
  $("#live-empty").hidden = st.streamers.length > 0;
  const seen = new Set();
  for (const s of st.streamers) {
    seen.add(String(s.id));
    let card = grid.querySelector(`[data-id="${s.id}"]`);
    if (!card) {
      card = document.createElement("div");
      card.className = "card live-card";
      card.dataset.id = s.id;
      grid.appendChild(card);
    }
    renderLiveCard(card, s);
  }
  $$(".live-card", grid).forEach((c) => { if (!seen.has(c.dataset.id)) c.remove(); });
}

function renderLiveCard(card, s) {
  const live = s.status === "live" || s.status === "paused";
  const hype = Math.round(s.hype || 0);
  const pillCls = s.status === "live" ? "live" : (s.status === "paused" || s.status === "live_other") ? "paused" : s.status === "error" ? "error" : "";
  card.innerHTML = `
    <div class="head"><span class="pf ${s.platform}">${s.platform}</span><span class="name" title="${esc(s.url)}">${esc(s.name)}</span>
      <span class="pill ${pillCls}">${STATUS[s.status] || s.status}</span></div>
    <div class="title">${esc(s.title || s.error || "")}${s.category ? ` · <b>${esc(s.category)}</b>` : ""}</div>
    ${live ? `
      <div class="hype"><div class="num" style="color:${hype >= 72 ? "var(--accent)" : hype >= 50 ? "var(--accent2)" : "var(--text)"}">${hype}</div><canvas></canvas></div>
      <div class="meter"><div style="width:${hype}%"></div></div>
      <div class="stats">
        <div><b>${s.chat_per_min ?? 0}</b><span>Chat/min ${s.chat_connected ? "" : "⚠"}</span></div>
        <div><b>${s.lag ?? "-"}s</b><span>Chat-Lag</span></div>
        <div><b>${s.clips ?? 0}</b><span>Clips Session</span></div>
        <div><b>${s.started ? dur(Date.now() / 1000 - s.started) : "-"}</b><span>Läuft seit</span></div>
      </div>
      ${s.warmup ? `<p class="small muted">⏳ Lernt gerade den normalen Chat-Pegel (die ersten Minuten) …</p>` : ""}
      ${!s.recording ? `<p class="small" style="color:var(--warn)">⚠ Aufnahme verbindet neu …</p>` : ""}
      <div class="row"><button class="primary" data-act="clip">✂ Jetzt clippen (45s)</button></div>`
    : `<p class="small muted">${s.enabled ? "Wartet auf Stream-Start …" : "Deaktiviert"} · ${s.clips_24h} Clips in 24h</p>
       <div class="row"><button data-act="check">Jetzt prüfen</button></div>`}`;
  const btn = card.querySelector("[data-act]");
  btn?.addEventListener("click", async () => {
    try {
      if (btn.dataset.act === "clip") { await api("POST", `/api/streamers/${s.id}/clip`, { seconds: 45 }); toast("Clip wird erstellt …"); }
      else { await api("POST", `/api/streamers/${s.id}/check`); toast("Prüfe Live-Status …"); }
    } catch (e) { toast(e.message, true); }
  });
  if (live) drawHistory(card.querySelector("canvas"), s.id);
}

const histCache = {};
async function drawHistory(canvas, id) {
  const now = Date.now();
  if (!histCache[id] || now - histCache[id].t > 4000) {
    try { histCache[id] = { t: now, points: (await api("GET", `/api/streamers/${id}/history`)).points }; } catch (_) { return; }
  }
  const pts = histCache[id].points;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.strokeStyle = "rgba(255,255,255,.12)";
  ctx.setLineDash([3, 3]);
  const ty = h - (72 / 100) * h;
  ctx.beginPath(); ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke();
  ctx.setLineDash([]);
  if (pts.length < 2) return;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#ff4f8b"); grad.addColorStop(1, "#ffb84f");
  ctx.strokeStyle = grad; ctx.lineWidth = 1.6;
  ctx.beginPath();
  pts.forEach((p, i) => { const x = (i / (pts.length - 1)) * w, y = h - (p[1] / 100) * h; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.stroke();
}

/* ------------------------------------------------------------------ Clips */
$$("#clip-filter button").forEach((b) => b.addEventListener("click", () => {
  state.clipStatus = b.dataset.status;
  $$("#clip-filter button").forEach((x) => x.classList.toggle("active", x === b));
  $("#clip-list").innerHTML = ""; state.clipSig = {};
  loadClips();
}));
$("#clip-streamer").addEventListener("change", (e) => { state.clipStreamer = e.target.value; $("#clip-list").innerHTML = ""; state.clipSig = {}; loadClips(); });

async function loadClips() {
  const [clips, accounts, streamers] = await Promise.all([
    api("GET", `/api/clips?status=${state.clipStatus}${state.clipStreamer ? `&streamer_id=${state.clipStreamer}` : ""}`),
    api("GET", "/api/accounts"), api("GET", "/api/streamers")]);
  state.accounts = accounts; state.streamers = streamers;
  const sel = $("#clip-streamer");
  if (sel.options.length !== streamers.length + 1) {
    sel.innerHTML = `<option value="">Alle Streamer</option>` + streamers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    sel.value = state.clipStreamer;
  }
  const list = $("#clip-list");
  $("#clips-empty").hidden = clips.length > 0;
  const ids = new Set(clips.map((c) => String(c.id)));
  $$(".clip", list).forEach((el) => { if (!ids.has(el.dataset.id)) el.remove(); });
  clips.forEach((c, i) => {
    const sig = JSON.stringify([c.status, c.video_url, c.step, c.error, c.uploads.map((u) => [u.status, u.url])]);
    let el = list.querySelector(`.clip[data-id="${c.id}"]`);
    if (el && state.clipSig[c.id] === sig) return;
    if (el && el.classList.contains("dirty")) { updateClipStatus(el, c); state.clipSig[c.id] = sig; return; }
    const fresh = renderClip(c);
    if (el) el.replaceWith(fresh); else list.insertBefore(fresh, list.children[i] || null);
    state.clipSig[c.id] = sig;
  });
  const ready = clips.filter((c) => c.status === "ready").length;
  const badge = $("#badge-clips");
  badge.hidden = !ready; badge.textContent = ready;
}

function scoreClass(s) { return s >= 85 ? "" : s >= 70 ? "mid" : "low"; }

function renderClip(c) {
  const el = document.createElement("div");
  el.className = "card clip";
  el.dataset.id = c.id;
  const streamer = state.streamers.find((s) => s.id === c.streamer_id) || {};
  const targets = streamer.upload_targets && streamer.upload_targets.length ? streamer.upload_targets : state.accounts.map((a) => a.id);
  const bd = c.breakdown || {};
  const bar = (label, v) => `<span>${label}</span><div class="bar"><div style="width:${Math.max(0, Math.min(100, (v || 0) * 10))}%"></div></div><span>${(v ?? 0).toFixed ? v.toFixed(1) : v}</span>`;
  el.innerHTML = `
    <div>${c.video_url ? `<video src="${c.video_url}" poster="${c.thumb_url}" controls preload="none" playsinline></video>`
      : `<div class="placeholder">${esc(CLIP_STATUS[c.status])}<br>${esc(c.step || "")}</div>`}</div>
    <div>
      <div class="meta">
        <span class="score ${scoreClass(c.score)}">${Math.round(c.score || 0)}</span>
        ${c.llm_score ? `<span class="cat" title="${esc(bd.llm_reason || "")}">KI ${c.llm_score}/10</span>` : ""}
        <span class="cat">${CAT[c.category] || esc(c.category)}</span>
        <span class="cat">${esc(streamer.name || "?")}</span>
        <span class="small muted">${ago(c.created_at)} · ${c.duration ? Math.round(c.duration) + "s" : ""} ${c.source === "manual" ? "· manuell" : c.source === "vod" ? "· VOD" : ""}</span>
        <span class="small status-text">${esc(CLIP_STATUS[c.status])}${c.step ? " · " + esc(c.step) : ""}</span>
      </div>
      ${c.error ? `<div class="error-box">${esc(c.error)}</div>` : ""}
      <label>Titel</label><input type="text" data-f="title" value="${esc(c.title)}">
      <label>Hook (Einblendung am Anfang – nach Änderung "Neu rendern")</label><input type="text" data-f="hook" value="${esc(c.hook)}">
      <label>Caption</label><textarea data-f="caption" rows="4">${esc(c.caption)}</textarea>
      <label>Hashtags</label><input type="text" data-f="hashtags" value="${esc((c.hashtags || []).join(" "))}">
      <label>Hochladen auf</label>
      <div class="targets">${state.accounts.length ? state.accounts.map((a) => `<label><input type="checkbox" value="${a.id}" ${targets.includes(a.id) ? "checked" : ""}><span class="pf ${a.platform}">${a.platform}</span> ${esc(a.name)}</label>`).join("")
        : `<span class="small muted">Noch keine Accounts verbunden (Tab Accounts).</span>`}</div>
      <div class="row">
        <button class="primary" data-act="upload" ${c.video_url ? "" : "disabled"}>⬆ Hochladen</button>
        <button data-act="save">Speichern</button>
        <select data-f="layout" title="Layout"><option value="">Layout: Auto</option><option value="stack">Facecam oben</option><option value="crop">Nur Gameplay</option><option value="blur">Ganzes Bild + Blur</option></select>
        <button data-act="rerender">↻ Neu rendern</button>
        ${c.video_url ? `<a href="${c.video_url}" download><button type="button">⤓</button></a>` : ""}
        <button class="danger" data-act="discard">Verwerfen</button>
      </div>
      <div class="uploads">${uploadsHtml(c.uploads)}</div>
      <details><summary>Warum dieser Clip? (Signale, Chat, Transkript)</summary>
        <div class="bars">${bar("Chat-Rate", bd.chat_rate)}${bar("Chatter", bd.chatters)}${bar("Emotes", bd.emotes)}${bar("Lautstärke", bd.audio)}${bar("Bewegung", bd.motion)}</div>
        <p class="small muted">${bd.clip_requests ? `"Clip"-Rufe im Chat: ${bd.clip_requests} · ` : ""}Auslöser: ${bd.mode === "stream" ? "Stream (Ton/Bild)" : "Chat"} · Chat-Lag ${bd.lag ?? "-"}s${bd.vod_time ? ` · VOD-Zeit ${Math.floor(bd.vod_time / 60)}:${String(bd.vod_time % 60).padStart(2, "0")}` : ""}</p>
        ${(c.chat_sample || []).length ? `<div class="chat-sample">${c.chat_sample.map(([t, n]) => `<div>${n}× ${esc(t)}</div>`).join("")}</div>` : ""}
        ${c.transcript ? `<p class="small">${esc(c.transcript)}</p>` : ""}
      </details>
    </div>`;
  if (c.layout_override) el.querySelector('[data-f="layout"]').value = c.layout_override;
  $$("input[data-f], textarea[data-f]", el).forEach((i) => i.addEventListener("input", () => el.classList.add("dirty")));
  el.querySelector('[data-act="save"]').addEventListener("click", () => saveClip(el, c.id, true));
  el.querySelector('[data-act="upload"]').addEventListener("click", async () => {
    const ids = $$(".targets input:checked", el).map((i) => +i.value);
    if (!ids.length) return toast("Mindestens einen Account auswählen", true);
    try {
      await saveClip(el, c.id, false);
      await api("POST", `/api/clips/${c.id}/upload`, { account_ids: ids });
      toast("Upload gestartet");
      setTimeout(loadClips, 1200);
    } catch (e) { toast(e.message, true); }
  });
  el.querySelector('[data-act="rerender"]').addEventListener("click", async () => {
    try {
      await saveClip(el, c.id, false);
      await api("POST", `/api/clips/${c.id}/rerender`, { layout: el.querySelector('[data-f="layout"]').value || null });
      el.classList.remove("dirty");
      toast("Wird neu gerendert …"); loadClips();
    } catch (e) { toast(e.message, true); }
  });
  el.querySelector('[data-act="discard"]').addEventListener("click", async () => {
    if (!confirm("Clip verwerfen und Dateien löschen?")) return;
    await api("DELETE", `/api/clips/${c.id}`); el.remove(); toast("Verworfen");
  });
  return el;
}

function uploadsHtml(ups) {
  return (ups || []).map((u) => {
    const link = u.url && u.url.startsWith("http") ? `<a href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.url)}</a>` : esc(u.url || "");
    const label = { pending: "wartet", uploading: "lädt hoch …", done: "✓", failed: "✗" }[u.status] || u.status;
    return `<div><span class="pf ${u.platform}">${u.platform}</span> ${esc(u.account_name)}: <span class="st-${u.status}">${label}</span> ${link} ${u.error ? `<span class="st-failed">${esc(u.error)}</span>` : ""}</div>`;
  }).join("");
}

function updateClipStatus(el, c) {
  const s = el.querySelector(".status-text");
  if (s) s.textContent = (CLIP_STATUS[c.status] || c.status) + (c.step ? " · " + c.step : "");
  const u = el.querySelector(".uploads");
  if (u) u.innerHTML = uploadsHtml(c.uploads);
}

async function saveClip(el, id, notify) {
  const v = (f) => el.querySelector(`[data-f="${f}"]`).value;
  await api("PATCH", `/api/clips/${id}`, { title: v("title"), hook: v("hook"), caption: v("caption"),
    hashtags: v("hashtags").split(/\s+/).filter(Boolean).map((h) => (h.startsWith("#") ? h : "#" + h)) });
  el.classList.remove("dirty");
  if (notify) toast("Gespeichert");
}

/* ------------------------------------------------------------------ Streamer */
$("#add-streamer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const s = await api("POST", "/api/streamers", { url: f.get("url"), name: f.get("name"), language: f.get("language") });
    toast(`${s.name} hinzugefügt (${s.platform})`);
    e.target.reset();
    loadStreamers();
  } catch (err) { toast(err.message, true); }
});

async function loadStreamers() {
  const [streamers, accounts] = await Promise.all([api("GET", "/api/streamers"), api("GET", "/api/accounts")]);
  state.streamers = streamers; state.accounts = accounts;
  const list = $("#streamer-list");
  if (list.querySelector(".dirty")) return;
  list.innerHTML = streamers.length ? "" : `<p class="empty">Noch keine Streamer.</p>`;
  for (const s of streamers) list.appendChild(renderStreamer(s));
}

function renderStreamer(s) {
  const el = document.createElement("div");
  el.className = "card streamer";
  const set = s.settings || {};
  const det = set.detection || {};
  const fc = s.facecam === "none" ? "keine" : s.facecam ? "eingezeichnet" : "automatisch";
  const targets = s.upload_targets || [];
  el.innerHTML = `
    <div>
      <div class="row"><span class="pf ${s.platform}">${s.platform}</span><b style="font-size:16px">${esc(s.name)}</b>
        <a class="small muted" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a></div>
      <div class="settings">
        <label>Auslöse-Score<input type="number" min="40" max="99" data-s="detection.trigger_score" value="${det.trigger_score ?? ""}" placeholder="72"></label>
        <label>Max. Clips/Std<input type="number" min="1" max="60" data-s="detection.max_clips_per_hour" value="${det.max_clips_per_hour ?? ""}" placeholder="10"></label>
        <label>Max. Länge (s)<input type="number" min="15" max="180" data-s="clips.max_seconds" value="${(set.clips || {}).max_seconds ?? ""}" placeholder="60"></label>
        <label>Sprache<select data-s="transcription.language">
          ${[["", "Standard"], ["de", "Deutsch"], ["en", "Englisch"], ["tr", "Türkisch"], ["ar", "Arabisch"]].map(([v, l]) => `<option value="${v}" ${((set.transcription || {}).language ?? "") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Layout<select data-s="render.layout">
          ${[["", "Auto"], ["stack", "Facecam oben"], ["crop", "Nur Gameplay"], ["blur", "Ganzes Bild + Blur"]].map(([v, l]) => `<option value="${v}" ${((set.render || {}).layout ?? "") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Facecam: ${fc}<button type="button" data-act="facecam">Einzeichnen …</button></label>
      </div>
      <div class="small muted" style="margin-top:10px">Standard-Upload-Ziele:</div>
      <div class="targets">${accounts_checkboxes(targets)}</div>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
      <label class="switch"><input type="checkbox" data-act="enabled" ${s.enabled ? "checked" : ""}> aktiv</label>
      <button data-act="save" class="primary">Speichern</button>
      <button data-act="delete" class="danger">Entfernen</button>
    </div>`;
  $$("input, select", el).forEach((i) => i.addEventListener("change", () => el.classList.add("dirty")));
  el.querySelector('[data-act="save"]').addEventListener("click", async () => {
    const settings = {};
    $$("[data-s]", el).forEach((i) => {
      const [sec, key] = i.dataset.s.split(".");
      settings[sec] = settings[sec] || {};
      settings[sec][key] = i.value === "" ? null : (i.type === "number" ? +i.value : i.value);
    });
    const upload_targets = $$(".targets input:checked", el).map((i) => +i.value);
    try {
      await api("PATCH", `/api/streamers/${s.id}`, { settings, upload_targets, enabled: el.querySelector('[data-act="enabled"]').checked });
      el.classList.remove("dirty"); toast("Gespeichert"); loadStreamers();
    } catch (e) { toast(e.message, true); }
  });
  el.querySelector('[data-act="delete"]').addEventListener("click", async () => {
    if (!confirm(`${s.name} entfernen? (Clips bleiben erhalten)`)) return;
    await api("DELETE", `/api/streamers/${s.id}`); loadStreamers();
  });
  el.querySelector('[data-act="facecam"]').addEventListener("click", () => openFacecam(s));
  return el;
}

function accounts_checkboxes(selected) {
  if (!state.accounts.length) return `<span class="small muted">Noch keine Accounts – ohne Auswahl sind beim Upload alle vorausgewählt.</span>`;
  return state.accounts.map((a) => `<label><input type="checkbox" value="${a.id}" ${selected.includes(a.id) ? "checked" : ""}><span class="pf ${a.platform}">${a.platform}</span> ${esc(a.name)}</label>`).join("");
}

/* ------------------------------------------------------------------ Facecam-Dialog */
let fc = { streamer: null, rect: null, drag: null };
function openFacecam(s) {
  fc = { streamer: s, rect: s.facecam && s.facecam.w ? { ...s.facecam } : null, drag: null };
  $("#fc-name").textContent = s.name;
  const img = $("#fc-img");
  img.onerror = () => { toast("Kein Bild verfügbar – Streamer muss live sein oder schon einen Clip haben", true); $("#facecam-dlg").close(); };
  img.onload = drawFc;
  img.src = `/api/streamers/${s.id}/snapshot?t=${Date.now()}`;
  $("#facecam-dlg").showModal();
}
function drawFc() {
  const c = $("#fc-canvas"), img = $("#fc-img");
  c.width = img.clientWidth; c.height = img.clientHeight;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!fc.rect) return;
  const r = fc.rect, x = r.x * c.width, y = r.y * c.height, w = r.w * c.width, h = r.h * c.height;
  ctx.fillStyle = "rgba(0,0,0,.45)"; ctx.fillRect(0, 0, c.width, c.height);
  ctx.clearRect(x, y, w, h);
  ctx.strokeStyle = "#ff4f8b"; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
}
const fcPos = (e) => { const b = $("#fc-canvas").getBoundingClientRect(); return { x: (e.clientX - b.left) / b.width, y: (e.clientY - b.top) / b.height }; };
$("#fc-canvas").addEventListener("pointerdown", (e) => { fc.drag = fcPos(e); e.target.setPointerCapture(e.pointerId); });
$("#fc-canvas").addEventListener("pointermove", (e) => {
  if (!fc.drag) return;
  const p = fcPos(e), clamp = (v) => Math.max(0, Math.min(1, v));
  fc.rect = { x: clamp(Math.min(p.x, fc.drag.x)), y: clamp(Math.min(p.y, fc.drag.y)), w: Math.abs(p.x - fc.drag.x), h: Math.abs(p.y - fc.drag.y) };
  drawFc();
});
$("#fc-canvas").addEventListener("pointerup", () => (fc.drag = null));
async function saveFacecam(body) {
  try {
    await api("PUT", `/api/streamers/${fc.streamer.id}/facecam`, body);
    $("#facecam-dlg").close(); toast("Facecam gespeichert – gilt für neue Clips (alte: Neu rendern)"); loadStreamers();
  } catch (e) { toast(e.message, true); }
}
$("#fc-save").addEventListener("click", () => {
  if (!fc.rect || fc.rect.w < 0.03 || fc.rect.h < 0.03) return toast("Erst einen Rahmen ziehen", true);
  saveFacecam({ mode: "rect", rect: fc.rect });
});
$("#fc-none").addEventListener("click", () => saveFacecam({ mode: "none" }));
$("#fc-auto").addEventListener("click", () => saveFacecam({ mode: "auto" }));

/* ------------------------------------------------------------------ Accounts */
async function loadAccounts() {
  const accounts = await api("GET", "/api/accounts");
  state.accounts = accounts;
  $("#account-list").innerHTML = accounts.length ? accounts.map((a) => `
    <div class="row"><span class="pf ${a.platform}">${a.platform}</span><b>${esc(a.name)}</b>
      <span class="small muted">verbunden ${ago(a.created_at)}</span>
      <button class="danger" data-del="${a.id}" style="margin-left:auto">Trennen</button></div>`).join("")
    : `<p class="small muted">Noch keine Accounts verbunden.</p>`;
  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Account trennen?")) return;
    await api("DELETE", `/api/accounts/${b.dataset.del}`); loadAccounts();
  }));
}
$("#ig-add").addEventListener("click", async () => {
  try { const r = await api("POST", "/api/accounts/instagram", { token: $("#ig-token").value }); toast(`Instagram ${r.name} verbunden`); $("#ig-token").value = ""; loadAccounts(); }
  catch (e) { toast(e.message, true); }
});
$("#tiktok-link").addEventListener("click", async () => {
  try { window.open((await api("GET", "/api/accounts/tiktok/auth-url")).url, "_blank"); } catch (e) { toast(e.message, true); }
});
$("#tiktok-add").addEventListener("click", async () => {
  try { const r = await api("POST", "/api/accounts/tiktok", { redirected_url: $("#tiktok-url").value }); toast(`TikTok ${r.name} verbunden`); $("#tiktok-url").value = ""; loadAccounts(); }
  catch (e) { toast(e.message, true); }
});

/* ------------------------------------------------------------------ Start */
const initial = location.hash.slice(1);
showTab(["live", "clips", "streamers", "accounts"].includes(initial) ? initial : "live");
setInterval(() => { if (state.tab === "live") loadLive(); }, 2000);
setInterval(() => { if (state.tab === "clips") loadClips(); }, 6000);
