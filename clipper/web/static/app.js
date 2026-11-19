"use strict";

/* ClipStream Dashboard – Live, Clips (Liste + Player), Streamer, Accounts, Einstellungen */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id) => `<svg><use href="#i-${id}"/></svg>`;

const state = {
  view: "live", info: null, status: null, streamers: [], accounts: [], clips: [], settings: null,
  clipFilter: "ready", clipStreamer: "", selected: null, detailSig: "", addPlatform: "twitch", openStreamers: new Set(),
};

const CAT = { funny: "😂 Lustig", hype: "🔥 Hype", shock: "😳 Schock", fail: "💀 Fail", action: "💥 Action",
  easteregg: "🥚 Easter Egg", glitch: "🤖 Glitch", spannung: "😶 Spannung", stichwort: "🔑 Stichwort" };
const DISCARD_REASONS = [["spam", "Nur Chat-Spam"], ["boring", "Langweilig"], ["cut", "Falscher Ausschnitt"], ["other", "Anderes"]];
const LAYOUTS = [["auto", "Automatisch"], ["stack", "Facecam oben"], ["stack_chat", "Facecam + Chat oben"],
  ["crop", "Nur Gameplay"], ["crop_chat", "Gameplay + Chat"], ["blur", "Ganzes Bild"]];
const CLIP_STATUS = { queued: "Wartet", processing: "Wird verarbeitet", ready: "Bereit", failed: "Fehler",
  uploaded: "Hochgeladen", filtered: "Aussortiert", discarded: "Verworfen" };
const SOURCE = { auto: "", vod: "VOD", manual: "Manuell", special: "Spezial-Moment" };
const PF = { twitch: "Twitch", youtube: "YouTube", kick: "Kick", local: "Test", tiktok: "TikTok", instagram: "Instagram" };

async function api(method, url, body) {
  const res = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* leer */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), 2600);
}

const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.floor(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.floor(s / 3600)} Std.`;
  return new Date(ts * 1000).toLocaleDateString("de-DE", { day: "numeric", month: "short" });
};
const since = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 3600 ? `${Math.floor(s / 60)} Min.` : `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} Std.`;
};
const streamerById = (id) => state.streamers.find((s) => s.id === id) || {};
// Empfindlichkeit: Regler links = weniger Clips (hohe Schwelle), rechts = mehr Clips
const triggerToSlider = (t) => Math.round(Math.min(100, Math.max(0, (90 - t) / 0.35)));
const sliderToTrigger = (v) => Math.round(90 - v * 0.35);

/* ================================================================== Navigation */
$$("nav a").forEach((a) => a.addEventListener("click", () => show(a.dataset.view)));
document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g) show(g.dataset.goto); });
window.addEventListener("hashchange", () => {
  const v = location.hash.slice(1);
  if (v !== state.view && document.getElementById(`view-${v}`)) show(v);
});

function show(view) {
  state.view = view;
  $$("nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("on", v.id === `view-${view}`));
  if (location.hash.slice(1) !== view) history.replaceState(null, "", `#${view}`);
  refresh();
}

async function refresh() {
  try {
    if (state.view === "live") await loadStatus();
    if (state.view === "clips") await loadClips();
    if (state.view === "streamers") await loadStreamers();
    if (state.view === "accounts") await loadAccounts();
    if (state.view === "settings") await loadSettings();
  } catch (e) { console.error(e); }
}

/* ================================================================== Status + Live */
async function loadInfo() {
  try { state.info = await api("GET", "/api/info"); } catch (_) { state.info = null; }
}

async function loadStatus() {
  const st = await api("GET", "/api/status");
  state.status = st;
  const live = st.streamers.filter((s) => s.status === "live").length;
  const ready = (st.clips || {}).ready || 0;
  setBadge("#n-live", live);
  setBadge("#n-clips", ready);
  renderSidebar(st);
  if (state.view === "live") renderLive(st);
  return st;
}

function setBadge(sel, n) { const b = $(sel); b.hidden = !n; b.textContent = n; }

function renderSidebar(st) {
  const working = Object.keys(st.processing || {}).length;
  const info = state.info || {};
  const lines = [];
  lines.push(`<div><span class="dot green"></span>Läuft${info.started ? ` seit ${since(info.started)}` : ""}</div>`);
  lines.push(`<div><span class="dot ${working ? "orange" : ""}"></span>${working ? `Rendert ${working} Clip${working > 1 ? "s" : ""}` : "Nichts zu rendern"}${st.queue ? ` · ${st.queue} wartend` : ""}</div>`);
  if (info.lan_url) lines.push(`<div>${icon("wifi")}<span>${esc(info.lan_url.replace("http://", ""))}</span></div>`);
  $("#sys-status").innerHTML = lines.join("");
  $("#sys-warn").hidden = !!info.password || !info.lan_url;
}

function renderLive(st) {
  const grid = $("#live-grid");
  $("#live-empty").hidden = st.streamers.length > 0;
  const live = st.streamers.filter((s) => s.status === "live").length;
  $("#live-sub").textContent = st.streamers.length ? `${live} von ${st.streamers.length} live` : "";
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

function statusPill(s) {
  const map = { live: ["red", "Live"], paused: ["orange", "Live · andere Kategorie"], live_other: ["orange", "Live · andere Kategorie"],
    offline: ["", "Offline"], checking: ["", "Prüfe …"], error: ["red", "Fehler"], disabled: ["", "Pausiert"] };
  const [c, t] = map[s.status] || ["", s.status];
  return `<span class="pill"><span class="dot ${c}"></span>${t}</span>`;
}

function renderLiveCard(card, s) {
  const live = s.status === "live" || s.status === "paused";
  const hype = Math.round(s.hype || 0);
  const trig = s.trigger || 72;
  const sub = s.status === "error" ? (s.error || "Fehler") : [s.category, s.title].filter(Boolean).join(" · ");
  card.innerHTML = `
    <div class="top"><div class="avatar">${esc((s.name || "?")[0].toUpperCase())}</div>
      <div class="who"><b>${esc(s.name)}</b><span class="pf">${PF[s.platform] || s.platform}</span></div>${statusPill(s)}</div>
    <div class="sub" title="${esc(sub)}">${esc(sub) || "&nbsp;"}</div>
    ${live ? `
      <div class="hype"><div class="num" style="color:${hype >= trig ? "var(--accent)" : "inherit"}">${hype}<small>${s.warmup ? "lernt Chat-Pegel …" : "Hype"}</small></div><canvas></canvas></div>
      <div class="stats"><span><b>${s.chat_per_min ?? 0}</b> Chat/Min.${s.chat_connected ? "" : " ⚠"}</span><span><b>${s.clips ?? 0}</b> Clips</span><span>seit <b>${s.started ? since(s.started) : "–"}</b></span></div>
      ${s.recording === false ? `<div class="small" style="color:var(--orange)">Aufnahme verbindet neu …</div>` : ""}
      <div class="actions"><button class="btn primary" data-act="clip">${icon("scissors")}Jetzt clippen</button></div>`
    : `<div class="stats"><span><b>${s.clips_24h ?? 0}</b> Clips in 24 Std.</span></div>
       <div class="actions"><button class="btn" data-act="check">Jetzt prüfen</button></div>`}`;
  const btn = card.querySelector("[data-act]");
  btn?.addEventListener("click", async () => {
    try {
      if (btn.dataset.act === "clip") { await api("POST", `/api/streamers/${s.id}/clip`, { seconds: 45 }); toast("Clip der letzten 45 Sek. wird erstellt"); }
      else { await api("POST", `/api/streamers/${s.id}/check`); toast("Prüfe, ob live …"); }
    } catch (e) { toast(e.message, true); }
  });
  if (live) drawHistory(card.querySelector("canvas"), s.id, trig);
}

const histCache = {};
async function drawHistory(canvas, id, trig) {
  const now = Date.now();
  if (!histCache[id] || now - histCache[id].t > 4000) {
    try { histCache[id] = { t: now, points: (await api("GET", `/api/streamers/${id}/history`)).points }; } catch (_) { return; }
  }
  const pts = histCache[id].points.slice(-600);
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const ty = h - (trig / 100) * h;
  ctx.strokeStyle = css.getPropertyValue("--line2"); ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke(); ctx.setLineDash([]);
  if (pts.length < 2) return;
  const accent = css.getPropertyValue("--accent").trim();
  ctx.beginPath();
  pts.forEach((p, i) => { const x = (i / (pts.length - 1)) * w, y = h - (p[1] / 100) * h; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = accent; ctx.lineWidth = 1.6; ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
  ctx.globalAlpha = 0.1; ctx.fillStyle = accent; ctx.fill(); ctx.globalAlpha = 1;
}

/* ================================================================== Clips */
$$("#clip-filter button").forEach((b) => b.addEventListener("click", () => {
  state.clipFilter = b.dataset.status;
  $$("#clip-filter button").forEach((x) => x.classList.toggle("on", x === b));
  state.selected = null;
  loadClips();
}));
$("#clip-streamer").addEventListener("change", (e) => { state.clipStreamer = e.target.value; state.selected = null; loadClips(); });

async function loadClips() {
  const q = `status=${state.clipFilter}${state.clipStreamer ? `&streamer_id=${state.clipStreamer}` : ""}&limit=150`;
  const [clips, accounts, streamers, st] = await Promise.all([api("GET", `/api/clips?${q}`), api("GET", "/api/accounts"),
    api("GET", "/api/streamers"), loadStatus()]);
  state.clips = clips; state.accounts = accounts; state.streamers = streamers;
  const counts = st.clips || {};
  const n = { ready: counts.ready || 0, working: (counts.queued || 0) + (counts.processing || 0) + (counts.failed || 0), uploaded: counts.uploaded || 0,
    filtered: counts.filtered || 0 };
  $$("#clip-filter button").forEach((b) => {
    const label = { ready: "Bereit", working: "In Arbeit", uploaded: "Hochgeladen", filtered: "Aussortiert", all: "Alle" }[b.dataset.status];
    const c = n[b.dataset.status];
    b.innerHTML = esc(label) + (c ? `<em>${c}</em>` : "");
  });
  const sel = $("#clip-streamer");
  if (sel.options.length !== streamers.length + 1) {
    sel.innerHTML = `<option value="">Alle Streamer</option>` + streamers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    sel.value = state.clipStreamer;
  }
  renderClipList();
  if (state.selected && !clips.some((c) => c.id === state.selected)) state.selected = null;
  if (!state.selected && clips.length && window.innerWidth > 860) state.selected = clips[0].id;
  renderDetail();
}

function scoreClass(s) { return s >= 85 ? "hi" : s >= 70 ? "mid" : ""; }

function renderClipList() {
  const list = $("#clip-list");
  const empty = { ready: "Keine fertigen Clips – sobald jemand live ist, landen sie hier.", working: "Gerade wird nichts gerendert.",
    uploaded: "Noch nichts hochgeladen.", filtered: "Nichts aussortiert.", all: "Noch keine Clips." }[state.clipFilter];
  $("#clips-empty").hidden = state.clips.length > 0;
  $("#clips-empty-text").textContent = empty;
  list.innerHTML = state.clips.map((c) => {
    const st = streamerById(c.streamer_id);
    const sub = [st.name, CAT[c.category] || c.category, c.status === "filtered" ? c.filter_reason : ago(c.created_at)].filter(Boolean).join(" · ");
    const thumb = c.thumb_url ? `style="background-image:url('${c.thumb_url}')"` : "";
    const busy = c.status === "queued" || c.status === "processing";
    return `<div class="clip-row${c.id === state.selected ? " on" : ""}" data-id="${c.id}">
      <div class="thumb" ${thumb}>${c.thumb_url ? "" : busy ? '<span class="spinner"></span>' : icon("film")}</div>
      <div class="txt"><div class="t1">${esc(c.title || CLIP_STATUS[c.status])}</div><div class="t2">${esc(sub)}</div></div>
      <span class="score ${scoreClass(c.score)}">${Math.round(c.score || 0)}</span></div>`;
  }).join("");
  $$(".clip-row", list).forEach((r) => r.addEventListener("click", () => selectClip(+r.dataset.id)));
}

function selectClip(id) {
  state.selected = id;
  state.detailSig = "";
  $$(".clip-row").forEach((r) => r.classList.toggle("on", +r.dataset.id === id));
  $(`.clip-row[data-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
  $("#view-clips").classList.add("detail-open");
  renderDetail();
}

function clipSig(c) {
  return c ? JSON.stringify([c.id, c.status, c.video_url, c.step, c.error, c.filter_reason, (c.uploads || []).map((u) => [u.status, u.url])]) : "";
}

function renderDetail() {
  const pane = $("#clip-detail");
  const c = state.clips.find((x) => x.id === state.selected);
  const sig = clipSig(c);
  if (sig === state.detailSig) return;
  if (pane.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) return;
  state.detailSig = sig;
  if (!c) {
    pane.innerHTML = `<div class="empty">${icon("film")}<p>Clip auswählen</p><p class="muted small">↑ ↓ wechseln · Leertaste abspielen</p></div>`;
    $("#view-clips").classList.remove("detail-open");
    return;
  }
  const st = streamerById(c.streamer_id);
  const bd = c.breakdown || {};
  const q = bd.quality || {};
  const opts = Object.assign({ layout: "auto", cam_size: "m" }, (c.layout || {}).opts || {}, c.render_opts || {});
  const busy = c.status === "queued" || c.status === "processing";
  const targets = (st.upload_targets || []).length ? st.upload_targets : state.accounts.map((a) => a.id);
  const meta = `<span class="score ${scoreClass(c.score)}">${Math.round(c.score || 0)}</span><span>` + [CAT[c.category] || esc(c.category),
    esc(st.name || ""), c.duration ? `${Math.round(c.duration)} Sek.` : "", ago(c.created_at), SOURCE[c.source] || "",
    c.llm_score ? `KI ${c.llm_score}/10` : ""].filter(Boolean).join(" · ") + "</span>";
  const bar = (label, v) => `<span>${label}</span><div class="bar"><div style="width:${Math.max(0, Math.min(100, (v || 0) * 10))}%"></div></div><span>${(+v || 0).toFixed(1)}</span>`;
  const pct = (v) => `${Math.round((v || 0) * 100)} %`;
  pane.innerHTML = `
    <button class="btn plain back" data-act="back">${icon("back")}Clips</button>
    <div class="detail">
      <div class="player">${c.video_url ? `<video src="${c.video_url}" poster="${c.thumb_url || ""}" controls playsinline preload="metadata"></video>`
        : `<div class="ph"><div>${busy ? '<span class="spinner"></span>' : icon("film")}<span>${esc(CLIP_STATUS[c.status])}${c.step ? ` · ${esc(c.step)}` : ""}</span></div></div>`}</div>
      <div class="form">
        <div class="head"><h2>${esc(c.title || CLIP_STATUS[c.status])}</h2><div class="meta">${meta}</div></div>
        ${c.status === "filtered" ? `<div class="banner yellow"><div class="grow"><b>Aussortiert:</b> ${esc(c.filter_reason || "")}</div>
          <button class="btn" data-act="force">Trotzdem rendern</button></div>` : ""}
        ${c.status === "failed" ? `<div class="banner red"><div class="grow">${esc(c.error || "Fehler")}</div></div>` : ""}
        ${busy ? `<div class="banner blue"><span class="spinner"></span><div class="grow">${esc(CLIP_STATUS[c.status])}${c.step ? ` – ${esc(c.step)}` : " …"}</div></div>` : ""}
        <div class="field"><label>Titel</label><input data-f="title" value="${esc(c.title)}" placeholder="Titel"></div>
        <div class="field"><label>Einblendung am Anfang (Hook)</label><input data-f="hook" value="${esc(c.hook)}" placeholder="z.B. WARTE AUF DAS ENDE"></div>
        <div class="field"><label>Caption</label><textarea data-f="caption" rows="4">${esc(c.caption)}</textarea></div>
        <div class="field"><label>Hashtags</label><input data-f="hashtags" value="${esc((c.hashtags || []).join(" "))}"></div>
        <div class="field"><label>Hochladen auf</label>
          <div class="targets">${state.accounts.length ? state.accounts.map((a) => `<label class="chip${targets.includes(a.id) ? " on" : ""}"><input type="checkbox" value="${a.id}" ${targets.includes(a.id) ? "checked" : ""}>${esc(PF[a.platform] || a.platform)} · ${esc(a.name)}</label>`).join("")
            : `<span class="muted small">Noch keine Accounts verbunden – <a href="#accounts" data-goto="accounts">verbinden</a>.</span>`}</div></div>
        <div class="upload-bar"><button class="btn primary big" data-act="upload" ${c.video_url && state.accounts.length ? "" : "disabled"}>${icon("up")}Hochladen</button>
          <div class="uploads">${uploadsHtml(c.uploads)}</div></div>
        ${c.long_url ? `<p class="muted small" style="margin:-6px 0 0">TikTok bekommt die lange Fassung (${Math.round(c.long_duration || 0)} Sek., über 1 Minute) · <a href="${c.long_url}" target="_blank" rel="noopener">ansehen</a></p>` : ""}
        <div class="layout-box">
          <div class="inline"><b style="flex:1">Layout</b>
            <select data-o="layout">${LAYOUTS.map(([v, l]) => `<option value="${v}" ${opts.layout === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
          <div class="inline"><span class="muted small" style="flex:1">Facecam-Größe</span>
            <div class="seg" data-o="cam_size">${[["s", "Klein"], ["m", "Mittel"], ["l", "Groß"]].map(([v, l]) => `<button type="button" data-v="${v}" class="${opts.cam_size === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
          <div class="inline"><span class="muted small" style="flex:1">Danach neu rendern (dauert ca. 20–60 Sek.)</span>
            <button class="btn" data-act="rerender" ${busy ? "disabled" : ""}>${icon("redo")}Neu rendern</button></div>
        </div>
        <details class="why"><summary>Warum dieser Clip?</summary>
          <div class="bars">${bar("Chat-Tempo", bd.chat_rate)}${bar("Emotes & Lachen", bd.emotes)}${bar("Verschiedene Chatter", bd.chatters)}${bar("Lautstärke", bd.audio)}${bar("Bewegung", bd.motion)}</div>
          <p class="muted">${bd.mode === "stream" ? "Ausgelöst vom Stream (Ton/Bild)." : "Ausgelöst vom Chat."}
            ${q.messages ? ` ${q.messages} Nachrichten · Emotes/Lachen ${pct(q.hype_ratio)} · Antworten ${pct(q.answer_ratio)} · Reaktion im Stream ${(q.stream_reaction || 0).toFixed(1)}` : ""}
            ${bd.clip_requests ? ` · ${bd.clip_requests}× „Clip“ im Chat` : ""}${bd.special_mentions ? ` · ${bd.special_mentions} Erwähnungen` : ""}${bd.silence_seconds ? ` · ${bd.silence_seconds} Sek. Stille` : ""}
            ${bd.vod_time ? ` · VOD-Zeit ${Math.floor(bd.vod_time / 60)}:${String(bd.vod_time % 60).padStart(2, "0")}` : ""}</p>
          ${(c.chat_sample || []).length ? `<div class="chat-sample">${c.chat_sample.map(([t, n]) => `<div>${n}× ${esc(t)}</div>`).join("")}</div>` : ""}
          ${bd.llm_reason ? `<p><b>KI ${c.llm_score ?? "–"}/10:</b> ${esc(bd.llm_reason)}</p>` : ""}
          ${c.transcript ? `<p>${esc(c.transcript)}</p>` : ""}
        </details>
        <div class="foot-actions">
          ${c.video_url ? `<a class="btn plain" href="${c.video_url}" download>${icon("down")}Herunterladen</a>` : ""}
          <span class="spacer"></span>
          <button class="btn plain danger" data-act="discard">${icon("trash")}${c.status === "filtered" ? "Löschen" : "Verwerfen"}</button>
        </div>
        <div class="discard-why" hidden><span class="muted small">Warum? ClipStream lernt daraus.</span>
          ${DISCARD_REASONS.map(([k, l]) => `<button class="btn" data-reason="${k}">${l}</button>`).join("")}
          <button class="btn plain" data-reason="">Abbrechen</button></div>
      </div>
    </div>`;
  bindDetail(pane, c);
}

function uploadsHtml(ups) {
  return (ups || []).map((u) => {
    const link = u.url && u.url.startsWith("http") ? `<a href="${esc(u.url)}" target="_blank" rel="noopener">öffnen</a>` : esc(u.url || "");
    const label = { pending: "wartet", uploading: "lädt hoch …", done: "✓ hochgeladen", failed: "✗ fehlgeschlagen" }[u.status] || u.status;
    return `<div>${esc(PF[u.platform] || u.platform)} · ${esc(u.account_name)}: <span class="st-${u.status}">${label}</span> ${link} ${u.error ? `<span class="st-failed">${esc(u.error)}</span>` : ""}</div>`;
  }).join("");
}

function fieldValues(pane) {
  const v = (f) => pane.querySelector(`[data-f="${f}"]`).value;
  return { title: v("title"), hook: v("hook"), caption: v("caption"),
    hashtags: v("hashtags").split(/\s+/).filter(Boolean).map((h) => (h.startsWith("#") ? h : "#" + h)) };
}

function bindDetail(pane, c) {
  const save = async () => {
    try {
      const upd = await api("PATCH", `/api/clips/${c.id}`, fieldValues(pane));
      Object.assign(c, upd);
      const row = $(`.clip-row[data-id="${c.id}"] .t1`);
      if (row) row.textContent = upd.title || "";
    } catch (e) { toast(e.message, true); }
  };
  $$("[data-f]", pane).forEach((el) => el.addEventListener("change", save));
  $$(".chip input", pane).forEach((i) => i.addEventListener("change", () => i.parentElement.classList.toggle("on", i.checked)));
  $$('.seg[data-o] button', pane).forEach((b) => b.addEventListener("click", () => {
    $$("button", b.parentElement).forEach((x) => x.classList.toggle("on", x === b));
  }));
  const act = (name, fn) => pane.querySelector(`[data-act="${name}"]`)?.addEventListener("click", fn);
  act("back", () => { $("#view-clips").classList.remove("detail-open"); state.selected = null; state.detailSig = ""; renderClipList(); });
  act("upload", async () => {
    const ids = $$(".chip input:checked", pane).map((i) => +i.value);
    if (!ids.length) return toast("Mindestens einen Account auswählen", true);
    try {
      await save();
      await api("POST", `/api/clips/${c.id}/upload`, { account_ids: ids });
      toast("Upload gestartet");
      setTimeout(loadClips, 1200);
    } catch (e) { toast(e.message, true); }
  });
  const rerender = async (msg) => {
    try {
      await save();
      const layout = pane.querySelector('[data-o="layout"]').value;
      const cam = pane.querySelector('[data-o="cam_size"] .on')?.dataset.v || "m";
      await api("POST", `/api/clips/${c.id}/rerender`, { layout, cam_size: cam });
      toast(msg);
      state.detailSig = "";
      loadClips();
    } catch (e) { toast(e.message, true); }
  };
  act("rerender", () => rerender("Wird neu gerendert …"));
  act("force", () => rerender("Wird trotzdem gerendert …"));
  const discard = async (reason) => {
    const res = await api("DELETE", `/api/clips/${c.id}${reason ? `?reason=${reason}` : ""}`);
    const idx = state.clips.findIndex((x) => x.id === c.id);
    state.clips.splice(idx, 1);
    state.selected = state.clips[Math.min(idx, state.clips.length - 1)]?.id ?? null;
    state.detailSig = "";
    toast(res.learned ? `Verworfen – ${res.learned}` : "Verworfen");
    renderClipList(); renderDetail();
  };
  act("discard", async () => {
    if (c.status === "filtered") { if (confirm("Aussortierten Clip löschen?")) await discard(""); return; }
    pane.querySelector(".discard-why").hidden = false;
  });
  $$("[data-reason]", pane).forEach((b) => b.addEventListener("click", () => {
    if (!b.dataset.reason) { pane.querySelector(".discard-why").hidden = true; return; }
    discard(b.dataset.reason).catch((e) => toast(e.message, true));
  }));
}

document.addEventListener("keydown", (e) => {
  if (state.view !== "clips" || e.target.matches("input, textarea, select") || e.metaKey || e.ctrlKey) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const idx = state.clips.findIndex((c) => c.id === state.selected);
    const next = state.clips[Math.max(0, Math.min(state.clips.length - 1, idx + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) selectClip(next.id);
  } else if (e.key === " ") {
    const v = $("#clip-detail video");
    if (v) { e.preventDefault(); v.paused ? v.play() : v.pause(); }
  }
});

/* ================================================================== Streamer */
$$("#add-platform button").forEach((b) => b.addEventListener("click", () => {
  state.addPlatform = b.dataset.pf;
  $$("#add-platform button").forEach((x) => x.classList.toggle("on", x === b));
  $('#add-streamer [name="url"]').placeholder = { twitch: "Handle, z.B. papaplatte", youtube: "Handle, z.B. @MontanaBlack", kick: "Handle, z.B. trymacs" }[b.dataset.pf];
}));
$("#add-streamer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const s = await api("POST", "/api/streamers", { url: f.get("url"), name: f.get("name"), platform: state.addPlatform });
    toast(`${s.name} hinzugefügt (${PF[s.platform] || s.platform})`);
    e.target.reset();
    loadStreamers();
  } catch (err) { toast(err.message, true); }
});

async function loadStreamers() {
  const [streamers, accounts] = await Promise.all([api("GET", "/api/streamers"), api("GET", "/api/accounts")]);
  state.streamers = streamers; state.accounts = accounts;
  const list = $("#streamer-list");
  if (list.contains(document.activeElement) && document.activeElement.matches("input, select")) return;
  list.innerHTML = streamers.length ? "" : `<div class="empty">${icon("users")}<p>Noch keine Streamer – oben Handle eintragen.</p></div>`;
  for (const s of streamers) list.appendChild(renderStreamer(s));
}

function renderStreamer(s) {
  const el = document.createElement("div");
  el.className = "card streamer-card" + (state.openStreamers.has(s.id) ? " open" : "");
  const set = s.settings || {};
  const det = set.detection || {}, rnd = set.render || {}, tr = set.transcription || {};
  const fc = s.facecam === "none" ? "keine" : s.facecam ? "eingezeichnet" : "automatisch";
  const targets = s.upload_targets || [];
  const trig = det.trigger_score;
  const opt = (pairs, cur) => pairs.map(([v, l]) => `<option value="${v}" ${String(cur ?? "") === v ? "selected" : ""}>${l}</option>`).join("");
  const layoutName = (LAYOUTS.find(([v]) => v === rnd.layout) || [null, "Standard-Layout"])[1];
  const share = det.stream_share;
  const summary = [trig ? `Schwelle ${trig}` : "Standard-Empfindlichkeit", layoutName, `Facecam ${fc}`].join(" · ");
  const learned = s.learned ? `<div class="row-item"><div class="lbl"><b>Automatisch angepasst</b><small>${esc(s.learned.note)} · ${ago(s.learned.time)}</small></div></div>` : "";
  el.innerHTML = `
    <div class="head"><div class="avatar">${esc(s.name[0].toUpperCase())}</div>
      <div class="who"><b>${esc(s.name)}</b> <span class="pf">${PF[s.platform] || s.platform}</span><span class="summary">${esc(summary)}</span></div>
      <svg class="chev"><use href="#i-back"/></svg>
      <label class="switch" title="Überwachen"><input type="checkbox" data-k="enabled" ${s.enabled ? "checked" : ""}><span></span></label></div>
    <div class="row-item"><div class="lbl"><b>Empfindlichkeit</b><small>${trig ? `Eigene Einstellung (Schwelle ${trig})` : "Wie in den Einstellungen"}</small></div>
      <div style="flex:1;max-width:320px"><div class="slider-row"><span>Weniger</span><input type="range" min="0" max="100" data-k="trigger" value="${triggerToSlider(trig || (state.settings?.["detection.trigger_score"] ?? 72))}"><span>Mehr Clips</span></div></div>
      ${trig ? `<button class="btn plain" data-k="trigger-reset">Standard</button>` : ""}</div>
    <div class="row-item"><div class="lbl"><b>Worauf achten?</b><small>${share != null ? `${100 - share} % Chat · ${share} % Stream (eigene Einstellung)` : "Wie in den Einstellungen"}</small></div>
      <div style="flex:1;max-width:320px"><div class="slider-row"><span>Chat</span><input type="range" min="0" max="100" step="5" data-k="share" value="${share ?? (state.settings?.["detection.stream_share"] ?? 40)}"><span>Stream</span></div></div>
      ${share != null ? `<button class="btn plain" data-k="share-reset">Standard</button>` : ""}</div>
    ${learned}
    <div class="row-item"><div class="lbl"><b>Sprache</b><small>Untertitel und Titel</small></div>
      <select data-s="transcription.language">${opt([["", "Standard"], ["de", "Deutsch"], ["en", "Englisch"], ["auto", "Automatisch"], ["tr", "Türkisch"], ["ar", "Arabisch"]], tr.language)}</select></div>
    <div class="row-item"><div class="lbl"><b>Layout</b><small>Für neue Clips dieses Streamers</small></div>
      <select data-s="render.layout">${opt([["", "Standard"], ...LAYOUTS], rnd.layout)}</select></div>
    <div class="row-item"><div class="lbl"><b>Facecam-Größe</b></div>
      <select data-s="render.cam_size">${opt([["", "Standard"], ["s", "Klein"], ["m", "Mittel"], ["l", "Groß"]], rnd.cam_size)}</select></div>
    <div class="row-item"><div class="lbl"><b>Facecam</b><small>${fc}</small></div>
      <button class="btn" data-k="facecam">${icon("camera")}Einzeichnen …</button></div>
    <div class="row-item col"><div class="lbl"><b>Standard-Upload-Ziele</b><small>Ohne Auswahl sind beim Hochladen alle Accounts angehakt.</small></div>
      <div class="targets">${state.accounts.length ? state.accounts.map((a) => `<label class="chip${targets.includes(a.id) ? " on" : ""}"><input type="checkbox" value="${a.id}" ${targets.includes(a.id) ? "checked" : ""}>${esc(PF[a.platform] || a.platform)} · ${esc(a.name)}</label>`).join("") : `<span class="muted small">Noch keine Accounts verbunden.</span>`}</div></div>
    <div class="row-item"><span class="spacer"></span><button class="btn plain danger" data-k="delete">${icon("trash")}Entfernen</button></div>`;
  const patch = async (body, msg = "Gespeichert") => {
    try { await api("PATCH", `/api/streamers/${s.id}`, body); toast(msg); } catch (e) { toast(e.message, true); }
  };
  el.querySelector(".head").addEventListener("click", (e) => {
    if (e.target.closest(".switch")) return;
    el.classList.toggle("open");
    if (el.classList.contains("open")) state.openStreamers.add(s.id); else state.openStreamers.delete(s.id);
  });
  el.querySelector('[data-k="enabled"]').addEventListener("change", (e) => patch({ enabled: e.target.checked }, e.target.checked ? "Wird überwacht" : "Pausiert"));
  el.querySelector('[data-k="trigger"]').addEventListener("change", async (e) => {
    await patch({ settings: { detection: { trigger_score: sliderToTrigger(+e.target.value) } } });
    loadStreamers();
  });
  el.querySelector('[data-k="share"]').addEventListener("change", async (e) => {
    await patch({ settings: { detection: { stream_share: +e.target.value } } });
    loadStreamers();
  });
  el.querySelector('[data-k="share-reset"]')?.addEventListener("click", async () => {
    await patch({ settings: { detection: { stream_share: null } } }, "Zurückgesetzt");
    loadStreamers();
  });
  el.querySelector('[data-k="trigger-reset"]')?.addEventListener("click", async () => {
    await patch({ settings: { detection: { trigger_score: null } } }, "Zurückgesetzt");
    loadStreamers();
  });
  $$("[data-s]", el).forEach((sel) => sel.addEventListener("change", () => {
    const [sec, key] = sel.dataset.s.split(".");
    patch({ settings: { [sec]: { [key]: sel.value || null } } });
  }));
  $$(".chip input", el).forEach((i) => i.addEventListener("change", () => {
    i.parentElement.classList.toggle("on", i.checked);
    patch({ upload_targets: $$(".chip input:checked", el).map((x) => +x.value) });
  }));
  el.querySelector('[data-k="facecam"]').addEventListener("click", () => openFacecam(s));
  el.querySelector('[data-k="delete"]').addEventListener("click", async () => {
    if (!confirm(`${s.name} entfernen? Fertige Clips bleiben erhalten.`)) return;
    await api("DELETE", `/api/streamers/${s.id}`);
    loadStreamers();
  });
  return el;
}

/* ================================================================== Facecam */
let fc = { streamer: null, rect: null, drag: null };
function openFacecam(s) {
  fc = { streamer: s, rect: s.facecam && s.facecam.w ? { ...s.facecam } : null, drag: null };
  $("#fc-name").textContent = s.name;
  const img = $("#fc-img");
  img.onerror = () => { toast("Kein Bild – der Streamer muss live sein oder schon einen Clip haben", true); $("#facecam-dlg").close(); };
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
  ctx.fillStyle = "rgba(0,0,0,.5)"; ctx.fillRect(0, 0, c.width, c.height);
  ctx.clearRect(x, y, w, h);
  ctx.strokeStyle = "#0a84ff"; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
}
const fcPos = (e) => { const b = $("#fc-canvas").getBoundingClientRect(); return { x: (e.clientX - b.left) / b.width, y: (e.clientY - b.top) / b.height }; };
$("#fc-canvas").addEventListener("pointerdown", (e) => { fc.drag = fcPos(e); e.target.setPointerCapture(e.pointerId); });
$("#fc-canvas").addEventListener("pointermove", (e) => {
  if (!fc.drag) return;
  const p = fcPos(e), cl = (v) => Math.max(0, Math.min(1, v));
  fc.rect = { x: cl(Math.min(p.x, fc.drag.x)), y: cl(Math.min(p.y, fc.drag.y)), w: Math.abs(p.x - fc.drag.x), h: Math.abs(p.y - fc.drag.y) };
  drawFc();
});
$("#fc-canvas").addEventListener("pointerup", () => (fc.drag = null));
async function saveFacecam(body) {
  try {
    await api("PUT", `/api/streamers/${fc.streamer.id}/facecam`, body);
    $("#facecam-dlg").close();
    toast("Facecam gespeichert – gilt für neue Clips");
    loadStreamers();
  } catch (e) { toast(e.message, true); }
}
$("#fc-save").addEventListener("click", () => {
  if (!fc.rect || fc.rect.w < 0.03 || fc.rect.h < 0.03) return toast("Erst einen Rahmen ziehen", true);
  saveFacecam({ mode: "rect", rect: fc.rect });
});
$("#fc-none").addEventListener("click", () => saveFacecam({ mode: "none" }));
$("#fc-auto").addEventListener("click", () => saveFacecam({ mode: "auto" }));

/* ================================================================== Accounts */
async function loadAccounts() {
  state.accounts = await api("GET", "/api/accounts");
  $("#account-list").innerHTML = state.accounts.length ? state.accounts.map((a) => `
    <div class="row-item"><span class="pf">${esc(PF[a.platform] || a.platform)}</span><div class="lbl"><b>${esc(a.name)}</b><small>verbunden ${ago(a.created_at)}</small></div>
      <button class="btn plain danger" data-del="${a.id}">Trennen</button></div>`).join("")
    : `<div class="row-item"><span class="muted">Noch keine Accounts verbunden.</span></div>`;
  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Account trennen?")) return;
    await api("DELETE", `/api/accounts/${b.dataset.del}`);
    loadAccounts();
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

/* ================================================================== Einstellungen */
// Strenge des Filters (0-100) <-> drei Schwellen; 50 entspricht den Standardwerten
const strictToFilter = (v) => ({ "filters.min_hype_ratio": +(0.10 + 0.30 * v / 100).toFixed(3),
  "filters.max_answer_ratio": +(0.65 - 0.40 * v / 100).toFixed(3), "filters.min_stream_reaction": +(0.4 + 1.2 * v / 100).toFixed(2) });
const filterToStrict = (s) => Math.round(Math.min(100, Math.max(0, ((s["filters.min_hype_ratio"] ?? 0.25) - 0.10) / 0.30 * 100)));
const WEIGHTS = [["chat_rate", "Chat-Tempo"], ["emotes", "Emotes & Lachen"], ["chatters", "Verschiedene Chatter"],
  ["audio", "Lautstärke"], ["motion", "Bewegung im Bild"]];

async function loadSettings() {
  state.settings = await api("GET", "/api/settings");
  renderSettings();
}

/* KI (Ollama): wird automatisch gefunden; Modell per Klick laden */
let llmTimer = null;
async function renderLlm() {
  const box = $("#llm-box");
  if (!box) return;
  clearTimeout(llmTimer);
  let st;
  try { st = await api("GET", "/api/llm/status"); } catch (e) { box.innerHTML = `<div class="row-item">${esc(e.message)}</div>`; return; }
  const s = state.settings || {};
  const pulling = st.pull && !st.pull.done;
  const pct = st.pull && st.pull.total ? Math.round((st.pull.completed / st.pull.total) * 100) : 0;
  let head;
  if (st.mode === "off") head = `<span class="dot"></span><div class="lbl"><b>Ausgeschaltet</b><small>Titel kommen aus Vorlagen</small></div>`;
  else if (st.active) head = `<span class="dot green"></span><div class="lbl"><b>Verbunden – ${esc(st.active)}</b><small>Schreibt Titel, Hook und Hashtags und bewertet jeden Moment</small></div>`;
  else if (st.reachable) head = `<span class="dot orange"></span><div class="lbl"><b>Ollama läuft, aber es ist noch kein Modell installiert</b><small>Einmal das empfohlene Modell laden (${esc(st.recommended_size)})</small></div>`;
  else head = `<span class="dot"></span><div class="lbl"><b>Ollama nicht gefunden</b><small>Unter ${esc(st.url)} antwortet nichts. Ollama starten (läuft nach der Installation im Hintergrund) – bis dahin gibt es Titel aus Vorlagen.</small></div>`;
  const needsModel = st.reachable && !st.models.some((m) => m.startsWith(st.recommended.split(":")[0]));
  box.innerHTML = `
    <div class="row-item">${head}</div>
    <div class="row-item"><div class="lbl"><b>Modus</b><small>Automatisch = benutzen, sobald Ollama läuft</small></div>
      <div class="seg" data-llm="mode">${[["auto", "Automatisch"], ["on", "An"], ["off", "Aus"]].map(([v, l]) => `<button type="button" data-v="${v}" class="${st.mode === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
    ${st.models.length > 1 ? `<div class="row-item"><div class="lbl"><b>Modell</b></div>
      <select data-llm="model">${st.models.map((m) => `<option ${m === st.active ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>` : ""}
    ${needsModel || pulling ? `<div class="row-item col"><div class="row-main" style="justify-content:space-between"><div class="lbl"><b>Empfohlenes Modell: ${esc(st.recommended)}</b><small>Gutes Deutsch, ${esc(st.recommended_size)} Download, läuft auf der Grafikkarte oder der CPU</small></div>
      <button class="btn primary" data-llm="pull" ${pulling ? "disabled" : ""}>${pulling ? "Lädt …" : "Laden"}</button></div>
      ${pulling ? `<div class="bar" style="height:8px"><div style="width:${pct}%"></div></div><small class="muted">${esc(st.pull.status || "")} ${pct ? `· ${pct} %` : ""}</small>` : ""}
      ${st.pull && st.pull.error ? `<small style="color:var(--red)">${esc(st.pull.error)}</small>` : ""}</div>` : ""}
    <div class="row-item"><div class="lbl"><b>Schwache Momente aussortieren</b><small>Wenn die KI einen Moment schlechter als diese Note (von 10) bewertet. 0 = aus</small></div>
      <input type="number" min="0" max="10" style="width:80px" data-llm="min_score" value="${s["llm.min_score"] ?? 3}"></div>
    <div class="row-item col"><div class="lbl"><b>Adresse</b><small>Nur ändern, wenn Ollama auf einem anderen Rechner läuft (z.B. dem Mac: http://192.168.…:11434)</small></div>
      <input data-llm="url" value="${esc(st.url)}"></div>
    <div class="row-item"><div class="lbl" id="llm-test-out"><small class="muted">Erzeugt einen Beispiel-Titel</small></div>
      <button class="btn" data-llm="test" ${st.active ? "" : "disabled"}>KI testen</button></div>`;
  const q = (k) => box.querySelector(`[data-llm="${k}"]`);
  $$('[data-llm="mode"] button', box).forEach((b) => b.addEventListener("click", async () => { await putSettings({ "llm.mode": b.dataset.v }); renderLlm(); }));
  q("model")?.addEventListener("change", async (e) => { await putSettings({ "llm.model": e.target.value }); renderLlm(); });
  q("min_score").addEventListener("change", (e) => putSettings({ "llm.min_score": +e.target.value }));
  q("url").addEventListener("change", async (e) => { await putSettings({ "llm.url": e.target.value.trim() }); renderLlm(); });
  q("pull")?.addEventListener("click", async () => {
    try { await api("POST", "/api/llm/pull", {}); toast("Modell wird geladen – das dauert ein paar Minuten"); renderLlm(); } catch (e) { toast(e.message, true); }
  });
  q("test")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    $("#llm-test-out").innerHTML = `<small class="muted"><span class="spinner"></span> KI denkt nach … (beim ersten Mal bis zu einer Minute)</small>`;
    try {
      const r = await api("POST", "/api/llm/test");
      $("#llm-test-out").innerHTML = `<b>${esc(r.title || "")}</b><small>Hook: ${esc(r.hook || "–")} · Bewertung ${r.score ?? "–"}/10${r.reason ? ` · ${esc(r.reason)}` : ""}</small>`;
    } catch (err) { $("#llm-test-out").innerHTML = `<small style="color:var(--red)">${esc(err.message)}</small>`; }
    e.target.disabled = false;
  });
  if (pulling && state.view === "settings") llmTimer = setTimeout(renderLlm, 1500);
}

async function putSettings(patch, msg = "Gespeichert") {
  try { state.settings = await api("PUT", "/api/settings", patch); toast(msg); } catch (e) { toast(e.message, true); }
}

function renderSettings() {
  const s = state.settings;
  const share = s["detection.stream_share"] ?? 35;
  const w = s["detection.weights"] || {};
  const cats = s["special.categories"] || [];
  const sw = (key, checked) => `<label class="switch"><input type="checkbox" data-b="${key}" ${checked ? "checked" : ""}><span></span></label>`;
  const balance = (v) => `${100 - v} % Chat · ${v} % Stream`;
  $("#settings").innerHTML = `
    <h3 class="group-title">Erkennung</h3>
    <div class="group">
      <div class="row-item col"><div class="lbl"><b>Worauf achten?</b><small id="bal-label">${balance(share)}</small></div>
        <div class="slider-row"><span>Chat</span><input type="range" min="0" max="100" step="5" data-r="share" value="${share}"><span>Stream</span></div>
        <small class="muted">Richtung Stream: Momente zählen nur, wenn der Streamer selbst reagiert (lauter, Action im Bild). Richtung Chat: der Chat entscheidet.</small></div>
      <div class="row-item col"><div class="lbl"><b>Empfindlichkeit</b><small>Schwelle ${s["detection.trigger_score"] ?? 72} – einzelne Streamer können abweichen</small></div>
        <div class="slider-row"><span>Weniger</span><input type="range" min="0" max="100" data-r="trigger" value="${triggerToSlider(s["detection.trigger_score"] ?? 72)}"><span>Mehr Clips</span></div></div>
      <div class="row-item"><div class="lbl"><b>Höchstens Clips pro Stunde</b><small>pro Streamer, darüber nur noch Ausnahme-Momente</small></div>
        <input type="number" min="1" max="60" style="width:80px" data-n="detection.max_clips_per_hour" value="${s["detection.max_clips_per_hour"] ?? 10}"></div>
      <div class="row-item col"><details><summary class="disclosure">Feinregler</summary>
        <p class="muted small">Gewichtung innerhalb von Chat und Stream. Das Verhältnis Chat ↔ Stream bestimmt der Regler oben.</p>
        ${WEIGHTS.map(([k, l]) => `<div class="slider-row" style="grid-template-columns:150px 1fr 40px;margin:6px 0"><span>${l}</span><input type="range" min="0" max="100" step="5" data-w="${k}" value="${Math.round((w[k] ?? 0) * 100)}"><span data-wl="${k}">${Math.round((w[k] ?? 0) * 100)}</span></div>`).join("")}
      </details></div>
    </div>

    <h3 class="group-title">Filter</h3>
    <div class="group">
      <div class="row-item"><div class="lbl"><b>Leere Chat-Ausbrüche aussortieren</b><small>z.B. wenn der Chat nur eine Frage beantwortet, bei Raids oder Bot-Spam. Aussortiertes landet unter Clips → Aussortiert.</small></div>${sw("filters.enabled", s["filters.enabled"])}</div>
      <div class="row-item col"><div class="lbl"><b>Strenge</b></div>
        <div class="slider-row"><span>Locker</span><input type="range" min="0" max="100" step="5" data-r="strict" value="${filterToStrict(s)}"><span>Streng</span></div></div>
      <div class="row-item"><div class="lbl"><b>Fragen an den Chat erkennen</b><small>Prüft im Transkript, ob der Streamer kurz vorher den Chat etwas gefragt hat</small></div>${sw("filters.question_check", s["filters.question_check"])}</div>
      <div class="row-item"><div class="lbl"><b>Aus Feedback lernen</b><small>Beim Verwerfen den Grund antippen, Hochladen zählt als gut – jeder Streamer wird dann behutsam nachjustiert</small></div>${sw("learn.enabled", s["learn.enabled"])}</div>
    </div>

    <h3 class="group-title">Spezial-Momente</h3>
    <div class="group">
      <div class="row-item"><div class="lbl"><b>Spezial-Momente clippen</b><small>Auch ohne großen Hype</small></div>${sw("special.enabled", s["special.enabled"])}</div>
      <div class="row-item"><div class="lbl"><b>🥚 Easter Eggs & Geheimnisse</b><small>Chat schreibt „Easter Egg“, „secret“, „Referenz“ …</small></div>${sw("cat:easteregg", cats.includes("easteregg"))}</div>
      <div class="row-item"><div class="lbl"><b>🤖 Glitches & Bugs</b><small>Chat schreibt „glitch“, „bug“, „kaputt“ …</small></div>${sw("cat:glitch", cats.includes("glitch"))}</div>
      <div class="row-item"><div class="lbl"><b>😶 Plötzliche Stille</b><small>Sonst aktiver Chat wird still – Spannung, Story, alle schauen gebannt</small></div>${sw("special.silence", s["special.silence"])}</div>
      <div class="row-item col"><div class="row-main" style="justify-content:space-between"><div class="lbl"><b>🔑 Eigene Stichwörter</b><small>Orte, Insider, Namen – fallen sie gehäuft im Chat, wird geclippt. Mit Komma trennen.</small></div>${sw("cat:stichwort", cats.includes("stichwort"))}</div>
        <input data-t="special.keywords" value="${esc((s["special.keywords"] || []).join(", "))}" placeholder="z.B. stripclub, ufo, polizei"></div>
    </div>

    <h3 class="group-title">Clips</h3>
    <div class="group">
      <div class="row-item"><div class="lbl"><b>Standard-Layout</b></div>
        <select data-c="render.layout">${LAYOUTS.map(([v, l]) => `<option value="${v}" ${s["render.layout"] === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="row-item"><div class="lbl"><b>Facecam-Größe</b></div>
        <div class="seg" data-g="render.cam_size">${[["s", "Klein"], ["m", "Mittel"], ["l", "Groß"]].map(([v, l]) => `<button type="button" data-v="${v}" class="${s["render.cam_size"] === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="row-item"><div class="lbl"><b>Chat einblenden</b><small>Bei „Automatisch“: neben der Facecam bzw. über dem Gameplay</small></div>${sw("render.chat_overlay", s["render.chat_overlay"])}</div>
      <div class="row-item"><div class="lbl"><b>Hook-Text am Anfang</b><small>z.B. „WARTE AUF DAS ENDE“</small></div>${sw("render.hook_text", s["render.hook_text"])}</div>
      <div class="row-item"><div class="lbl"><b>TikTok-Langversion</b><small>Zusätzlich eine Fassung über 1 Minute (nur solche Videos vergütet TikTok). TikTok-Uploads nehmen automatisch diese.</small></div>${sw("render.tiktok_long", s["render.tiktok_long"])}</div>
      <div class="row-item"><div class="lbl"><b>Maximale Länge</b><small>Sekunden</small></div>
        <input type="number" min="15" max="180" style="width:80px" data-n="clips.max_seconds" value="${s["clips.max_seconds"] ?? 60}"></div>
    </div>

    <h3 class="group-title">KI für Titel & Bewertung (Ollama)</h3>
    <div class="group" id="llm-box"><div class="row-item"><span class="spinner"></span><span class="muted">Suche Ollama …</span></div></div>

    <h3 class="group-title">Autopilot & Benachrichtigungen</h3>
    <div class="group">
      <div class="row-item"><div class="lbl"><b>Starke Clips automatisch hochladen</b><small>Auf die Standard-Upload-Ziele des Streamers (sonst alle Accounts). Manuelle und „trotzdem gerenderte“ Clips nie.</small></div>${sw("autopilot.enabled", s["autopilot.enabled"])}</div>
      <div class="row-item col"><div class="lbl"><b>Ab Score</b><small id="ap-label">${s["autopilot.min_score"] ?? 95}</small></div>
        <div class="slider-row"><span>80</span><input type="range" min="80" max="100" data-r="autopilot" value="${s["autopilot.min_score"] ?? 95}"><span>100</span></div></div>
      <div class="row-item"><div class="lbl"><b>Und KI-Bewertung mindestens</b><small>von 10 – gilt nur, wenn die KI läuft (0 = egal)</small></div>
        <input type="number" min="0" max="10" style="width:80px" data-n="autopilot.min_llm_score" value="${s["autopilot.min_llm_score"] ?? 6}"></div>
      <div class="row-item col"><div class="lbl"><b>Discord</b><small>Webhook-Adresse: Discord-Kanal → Bearbeiten → Integrationen → Webhooks → Neuer Webhook → URL kopieren</small></div>
        <div class="inline"><input type="password" data-sec="notify.discord_webhook" placeholder="${s["notify.discord_webhook"] ? "gesetzt ✓ – neu eintragen zum Ändern" : "https://discord.com/api/webhooks/…"}">
          ${s["notify.discord_webhook"] ? `<button class="btn plain danger" data-clear="notify.discord_webhook">Entfernen</button>` : ""}</div></div>
      <div class="row-item col"><div class="lbl"><b>Telegram</b><small>Bot über @BotFather anlegen → Token. Chat-ID z.B. über @userinfobot.</small></div>
        <div class="inline"><input type="password" data-sec="notify.telegram_token" placeholder="${s["notify.telegram_token"] ? "Token gesetzt ✓" : "Bot-Token"}">
          <input data-t="notify.telegram_chat_id" value="${esc(s["notify.telegram_chat_id"] || "")}" placeholder="Chat-ID" style="flex:0 1 160px">
          ${s["notify.telegram_token"] ? `<button class="btn plain danger" data-clear="notify.telegram_token">Entfernen</button>` : ""}</div></div>
      <div class="row-item"><div class="lbl"><b>Melden bei neuem Clip</b><small>ab Score ${s["notify.min_score"] ?? 0}</small></div>
        <input type="number" min="0" max="100" style="width:80px" data-n="notify.min_score" value="${s["notify.min_score"] ?? 0}">${sw("notify.on_ready", s["notify.on_ready"])}</div>
      <div class="row-item"><div class="lbl"><b>Melden bei Upload</b><small>Erfolg und Fehler</small></div>${sw("notify.on_upload", s["notify.on_upload"])}</div>
      <div class="row-item col"><div class="lbl"><b>Link in der Nachricht</b><small>Adresse, unter der ihr das Dashboard vom Handy erreicht (z.B. Tailscale). Leer = Adresse im WLAN.</small></div>
        <input data-t="notify.public_url" value="${esc(s["notify.public_url"] || "")}" placeholder="http://…:8686"></div>
      <div class="row-item"><span class="spacer"></span><button class="btn" data-act="notify-test">Test-Nachricht senden</button></div>
    </div>

    <h3 class="group-title">Allgemein</h3>
    <div class="group">
      <div class="row-item col"><div class="lbl"><b>Nur clippen bei Kategorie</b><small>Wörter aus Kategorie oder Stream-Titel, z.B. „gta, fortnite“. Leer = immer clippen.</small></div>
        <input data-t="monitor.only_keywords" value="${esc((s["monitor.only_keywords"] || []).join(", "))}" placeholder="leer = alles"></div>
      <div class="row-item col"><div class="lbl"><b>Hashtags</b><small>Kommen an jeden Clip, dazu automatisch Streamer und Kategorie</small></div>
        <input data-t="hashtags" value="${esc((s.hashtags || []).join(" "))}"></div>
    </div>`;
  const root = $("#settings");
  root.querySelector('[data-r="share"]').addEventListener("input", (e) => { $("#bal-label").textContent = balance(+e.target.value); });
  root.querySelector('[data-r="share"]').addEventListener("change", (e) => putSettings({ "detection.stream_share": +e.target.value }));
  root.querySelector('[data-r="trigger"]').addEventListener("change", async (e) => { await putSettings({ "detection.trigger_score": sliderToTrigger(+e.target.value) }); renderSettings(); });
  root.querySelector('[data-r="strict"]').addEventListener("change", (e) => putSettings(strictToFilter(+e.target.value)));
  root.querySelector('[data-r="autopilot"]').addEventListener("input", (e) => { $("#ap-label").textContent = e.target.value; });
  root.querySelector('[data-r="autopilot"]').addEventListener("change", (e) => putSettings({ "autopilot.min_score": +e.target.value }));
  $$("[data-sec]", root).forEach((i) => i.addEventListener("change", async () => {
    if (!i.value.trim()) return;
    await putSettings({ [i.dataset.sec]: i.value.trim() }, "Gespeichert – jetzt „Test-Nachricht senden“");
    renderSettings();
  }));
  $$("[data-clear]", root).forEach((b) => b.addEventListener("click", async () => {
    await putSettings({ [b.dataset.clear]: "" }, "Entfernt");
    renderSettings();
  }));
  root.querySelector('[data-act="notify-test"]').addEventListener("click", async () => {
    try { await api("POST", "/api/notify/test"); toast("Test-Nachricht gesendet"); } catch (e) { toast(e.message, true); }
  });
  $$("[data-w]", root).forEach((r) => {
    r.addEventListener("input", () => { root.querySelector(`[data-wl="${r.dataset.w}"]`).textContent = r.value; });
    r.addEventListener("change", () => putSettings({ "detection.weights": Object.fromEntries($$("[data-w]", root).map((x) => [x.dataset.w, +x.value / 100])) }));
  });
  $$("[data-b]", root).forEach((c) => c.addEventListener("change", () => {
    const k = c.dataset.b;
    if (k.startsWith("cat:")) {
      putSettings({ "special.categories": $$('[data-b^="cat:"]', root).filter((x) => x.checked).map((x) => x.dataset.b.slice(4)) });
    } else putSettings({ [k]: c.checked });
  }));
  $$("[data-n]", root).forEach((n) => n.addEventListener("change", () => putSettings({ [n.dataset.n]: +n.value })));
  $$("[data-c]", root).forEach((sel) => sel.addEventListener("change", () => putSettings({ [sel.dataset.c]: sel.value })));
  $$("[data-t]", root).forEach((t) => t.addEventListener("change", () => putSettings({ [t.dataset.t]: t.value })));
  $$(".seg[data-g] button", root).forEach((b) => b.addEventListener("click", () => {
    $$("button", b.parentElement).forEach((x) => x.classList.toggle("on", x === b));
    putSettings({ [b.parentElement.dataset.g]: b.dataset.v });
  }));
  renderLlm();
}

/* ================================================================== Start */
(async () => {
  await loadInfo();
  try { state.settings = await api("GET", "/api/settings"); } catch (_) { /* egal */ }
  loadStatus().catch(() => {});
  const initial = location.hash.slice(1);
  show(["live", "clips", "streamers", "accounts", "settings"].includes(initial) ? initial : "live");
  setInterval(() => { if (state.view === "live") loadStatus().catch(() => {}); }, 2000);
  setInterval(() => { if (state.view === "clips") loadClips().catch(() => {}); else if (state.view !== "live") loadStatus().catch(() => {}); }, 5000);
})();
