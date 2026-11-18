"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = {
  tab: "live", status: null, info: null, accounts: [], streamers: [],
  clipStatus: "ready", clipStreamer: "", clipSort: "new", editing: null, connected: true,
};

const CAT = { funny: "😂 Lustig", hype: "🔥 Hype", shock: "😳 Schock", fail: "💀 Fail", action: "💥 Action" };
const CLIP_STATUS = { queued: "In Warteschlange", processing: "Wird verarbeitet", ready: "Bereit", failed: "Fehler",
  uploaded: "Hochgeladen", discarded: "Verworfen" };
const PF_SHORT = { youtube: "YT", tiktok: "TT", instagram: "IG" };
const TABS = ["live", "clips", "streamers", "accounts"];
const LAYOUTS = [["", "Layout: Auto"], ["stack", "Facecam oben"], ["crop", "Nur Gameplay"], ["blur", "Ganzes Bild + Blur"]];
const EMPTY_CLIPS = { ready: "Gerade keine Clips bereit", working: "Nichts in Arbeit", uploaded: "Noch nichts hochgeladen",
  failed: "Keine Fehler 🎉", all: "Noch keine Clips" };

const icon = (id, cls = "") => `<svg class="i ${cls}"><use href="#i-${id}"/></svg>`;
const scene = (n) => `<svg class="scene"><use href="#scene-${"abc"[Math.abs(Number(n) || 0) % 3]}"/></svg>`;
const fmtNum = (n) => Number(n || 0).toLocaleString("de-DE");
const fmtSec = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const isLive = (s) => s.status === "live" || s.status === "paused";
const scoreClass = (s) => (s >= 85 ? "" : s >= 70 ? "mid" : "low");

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

function setText(root, key, value) {
  const el = root.querySelector(`[data-k="${key}"]`);
  if (el && el.textContent !== String(value)) el.textContent = value;
  return el;
}
function setCount(sel, value) {
  const el = $(sel);
  el.hidden = !value;
  el.textContent = value || "";
}
const streamerById = (id) => state.streamers.find((s) => s.id === id)
  || (state.status ? state.status.streamers.find((s) => s.id === id) : null);

/** Liste an Daten angleichen, ohne vorhandene Elemente neu zu bauen (verhindert Flackern). */
function syncList(box, items, create, update) {
  const keep = new Set(items.map((x) => String(x.id)));
  $$(":scope > [data-id]", box).forEach((el) => { if (!keep.has(el.dataset.id)) el.remove(); });
  items.forEach((x, i) => {
    let el = box.querySelector(`:scope > [data-id="${x.id}"]`);
    if (!el) { el = create(x); el.dataset.id = x.id; }
    if (box.children[i] !== el) box.insertBefore(el, box.children[i] || null);
    update(el, x);
  });
}

/* ------------------------------------------------------------------ Navigation */
$$(".nav").forEach((a) => a.addEventListener("click", () => showTab(a.dataset.tab)));
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) { e.preventDefault(); closeDrawer(); showTab(go.dataset.go); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.editing) closeDrawer(); });
window.addEventListener("hashchange", () => {
  const tab = location.hash.slice(1);
  if (TABS.includes(tab) && tab !== state.tab) { closeDrawer(); showTab(tab); }
});

function showTab(tab) {
  state.tab = tab;
  $$(".nav").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".page").forEach((s) => s.classList.toggle("active", s.id === `p-${tab}`));
  history.replaceState(null, "", `#${tab}`);
  refresh();
}

async function refresh() {
  try {
    if (state.tab === "live") { renderLive(); await loadLatest(); }
    if (state.tab === "clips") await loadClips();
    if (state.tab === "streamers") await loadStreamers();
    if (state.tab === "accounts") await loadAccounts();
  } catch (e) { console.error(e); }
}

/* ------------------------------------------------------------------ Status (Seitenleiste, Zähler) */
async function loadInfo() {
  try { state.info = await api("GET", "/api/info"); } catch (_) { return; }
  const lan = state.info.lan_url;
  $("#sys-lan-row").hidden = !lan;
  if (lan) $("#sys-lan").textContent = lan.replace(/^https?:\/\//, "");
  $("#warn-pw").hidden = !(lan && !state.info.password);
}

async function loadStatus() {
  let st;
  try { st = await api("GET", "/api/status"); } catch (e) {
    state.connected = false;
    $("#sys-dot").className = "dot err";
    $("#sys-state").textContent = "Keine Verbindung";
    $("#sys-since").textContent = "";
    throw e;
  }
  if (!state.connected) { state.connected = true; loadInfo(); }
  state.status = st;
  const live = st.streamers.filter(isLive);
  const c = st.clips || {};
  setCount("#n-live", live.length ? `${live.length} live` : "");
  setCount("#n-clips", c.ready || "");
  setCount("#n-streamers", st.streamers.length || "");
  setCount("#n-accounts", st.accounts || "");
  $("#sys-dot").className = "dot ok";
  $("#sys-state").textContent = "Läuft";
  $("#sys-since").textContent = state.info ? ` · seit ${dur(Date.now() / 1000 - state.info.started)}` : "";
  const working = Object.keys(st.processing).length;
  $("#sys-work").textContent = working ? `${working} Clip${working > 1 ? "s" : ""} in Arbeit${st.queue ? ` · ${st.queue} wartend` : ""}`
    : st.queue ? `${st.queue} Clips wartend` : "Leerlauf – nichts zu rendern";
  const counts = { ready: c.ready, working: (c.queued || 0) + (c.processing || 0), uploaded: c.uploaded, failed: c.failed };
  $$("#clip-filter i[data-c]").forEach((i) => { i.textContent = counts[i.dataset.c] || ""; });
}

/* ------------------------------------------------------------------ Live */
function renderLive() {
  const st = state.status;
  if (!st) return;
  const all = st.streamers;
  const live = all.filter(isLive);
  const off = all.filter((s) => !isLive(s));
  $("#live-sub").textContent = all.length
    ? `${all.length} Streamer ${all.length === 1 ? "wird" : "werden"} überwacht · ${live.length ? `${live.length} ${live.length === 1 ? "ist" : "sind"} gerade live` : "gerade ist niemand live"}`
    : "Noch keine Streamer eingetragen";
  $("#k-live").innerHTML = `${live.length} <small>/ ${all.length}</small>`;
  $("#k-live-sub").textContent = live.map((s) => s.name).join(", ") || "niemand live";
  $("#k-clips").textContent = all.reduce((a, s) => a + (s.clips_24h || 0), 0);
  $("#k-clips-sub").textContent = `${fmtNum((st.clips || {}).uploaded)} hochgeladen insgesamt`;
  const working = Object.keys(st.processing).length;
  $("#k-work").innerHTML = `${working} <small>· ${st.queue} wartend</small>`;
  $("#k-work-bar").classList.toggle("run", working > 0);
  $("#k-ready").textContent = (st.clips || {}).ready || 0;
  $("#live-empty").hidden = all.length > 0;
  syncList($("#live-grid"), live, liveCard, updateLiveCard);
  syncList($("#off-list"), off, offCard, updateOffCard);
}

function liveCard(s) {
  const el = document.createElement("div");
  el.className = "lc";
  el.innerHTML = `
    <div class="preview">${scene(s.id)}<img alt="">
      <div class="top"><span class="pf ${esc(s.platform)}">${esc(s.platform)}</span><span class="pill" data-k="pill"></span><span class="pill glass" data-k="cat"></span></div>
      <div class="bottom"><b>${esc(s.name)}</b><span data-k="title"></span></div></div>
    <div class="body">
      <div class="hype"><div class="num" data-k="numbox"><span data-k="num">0</span><small>HYPE</small></div>
        <svg class="chart" viewBox="0 0 300 64" preserveAspectRatio="none"></svg></div>
      <div class="stats"><div><b data-k="chat">0</b><span>Chat/min</span></div><div><b data-k="lag">–</b><span>Chat-Lag</span></div>
        <div><b data-k="clips">0</b><span>Clips</span></div><div><b data-k="since">–</b><span>Live seit</span></div></div>
      <div class="note" data-k="note"></div>
      <div class="acts"><button class="btn primary" data-act="clip">${icon("cut")}Jetzt clippen</button>
        <button class="btn" data-act="facecam" title="Facecam für diesen Streamer einzeichnen">${icon("cam")}Facecam</button></div>
    </div>`;
  el.querySelector('[data-act="clip"]').addEventListener("click", async () => {
    try { await api("POST", `/api/streamers/${s.id}/clip`, { seconds: 45 }); toast("Clip der letzten 45 Sekunden wird erstellt …"); }
    catch (e) { toast(e.message, true); }
  });
  el.querySelector('[data-act="facecam"]').addEventListener("click", () => openFacecam(streamerById(s.id) || s));
  const img = el.querySelector("img");
  img.addEventListener("load", () => img.classList.add("on"));
  el._snap = 0;
  return el;
}

function updateLiveCard(el, s) {
  const hype = Math.round(s.hype || 0);
  const trig = s.trigger || 72;
  const paused = s.status === "paused";
  const hot = hype >= trig && !paused;
  const pill = setText(el, "pill", paused ? "LIVE · kein GTA" : "● LIVE");
  pill.className = `pill ${paused ? "paused" : "live"}`;
  setText(el, "cat", s.category || "").hidden = !s.category;
  setText(el, "title", s.title || "");
  setText(el, "num", hype);
  el.querySelector('[data-k="numbox"]').classList.toggle("hotnum", hot);
  el.classList.toggle("hot", hot);
  setText(el, "chat", fmtNum(s.chat_per_min) + (s.chat_connected === false ? " ⚠" : ""));
  setText(el, "lag", s.lag != null ? `${Number(s.lag).toFixed(1).replace(".", ",")} s` : "–");
  setText(el, "clips", s.clips ?? 0);
  setText(el, "since", s.started ? dur(Date.now() / 1000 - s.started) : "–");
  let note = "", cls = "info";
  if (s.recording === false) { note = "⚠ Aufnahme verbindet neu …"; cls = "warn"; }
  else if (paused) { note = "Kein GTA in Titel/Kategorie – gerade wird nicht geclippt"; cls = "warn"; }
  else if (s.warmup) { note = "⏳ Lernt den normalen Pegel (erste ~3 Minuten) – noch keine Clips"; }
  else if (hot) { note = "🔥 Hype-Moment erkannt – Clip wird erstellt"; cls = "hot"; }
  const n = setText(el, "note", note);
  n.className = `note ${cls}`;
  // Standbild aus dem Stream (wird serverseitig alle 30 s erneuert)
  const img = el.querySelector("img");
  const every = img.classList.contains("on") ? 30000 : 8000;
  if (Date.now() - el._snap > every) {
    el._snap = Date.now();
    const url = `/api/streamers/${s.id}/snapshot?t=${Date.now()}`;
    const pre = new Image();
    pre.onload = () => { img.src = url; };
    pre.src = url;
  }
  drawHistory(el.querySelector("svg.chart"), s.id, trig);
}

const histCache = {};
async function drawHistory(svg, id, trig) {
  let h = histCache[id];
  if (!h || (!h.loading && Date.now() - h.t > 4000)) {
    h = histCache[id] = { points: h ? h.points : [], t: Date.now(), loading: true };
    try { h.points = (await api("GET", `/api/streamers/${id}/history`)).points; } catch (_) { /* ignore */ }
    h.loading = false;
  }
  paintChart(svg, h.points, trig, id);
}

function paintChart(svg, pts, trig, id) {
  const w = 300, hh = 64;
  const n = pts.length;
  let line = "";
  if (n >= 2) {
    const step = Math.max(1, Math.floor(n / 150));
    const s = [];
    for (let i = 0; i < n; i += step) s.push(pts[i]);
    if (s[s.length - 1] !== pts[n - 1]) s.push(pts[n - 1]);
    line = s.map((p, i) => `${i ? "L" : "M"}${(i / (s.length - 1) * w).toFixed(1)} ${(hh - Math.max(0, Math.min(100, p[1])) / 100 * hh).toFixed(1)}`).join(" ");
  }
  const ty = (hh - (trig / 100) * hh).toFixed(1);
  svg.innerHTML = `<defs>
      <linearGradient id="ha${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff3d7f" stop-opacity=".4"/><stop offset="1" stop-color="#ff9f43" stop-opacity="0"/></linearGradient>
      <linearGradient id="hl${id}" x1="0" x2="1"><stop offset="0" stop-color="#ff9f43"/><stop offset="1" stop-color="#ff3d7f"/></linearGradient></defs>
    <line x1="0" x2="${w}" y1="${ty}" y2="${ty}" stroke="#ffffff30" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
    <text x="${w - 2}" y="${Math.max(9, ty - 4)}" fill="#ffffff55" font-size="9" text-anchor="end">Auslöser ${trig}</text>
    ${line ? `<path d="${line} L${w} ${hh} L0 ${hh}Z" fill="url(#ha${id})"/>
    <path d="${line}" fill="none" stroke="url(#hl${id})" stroke-width="2" vector-effect="non-scaling-stroke"/>`
    : `<text x="4" y="${hh - 6}" fill="#ffffff40" font-size="9">Sammle Daten …</text>`}`;
}

function offCard(s) {
  const el = document.createElement("div");
  el.className = "off";
  el.innerHTML = `<div class="avatar ${esc(s.platform)}">${esc((s.name || "?")[0])}</div>
    <div class="info"><b>${esc(s.name)} <span class="pf ${esc(s.platform)}">${esc(s.platform)}</span></b><span data-k="txt"></span></div>
    <button class="btn sm" data-act="check">Jetzt prüfen</button>`;
  el.querySelector("[data-act]").addEventListener("click", async () => {
    try { await api("POST", `/api/streamers/${s.id}/check`); toast("Prüfe Live-Status …"); } catch (e) { toast(e.message, true); }
  });
  return el;
}

function updateOffCard(el, s) {
  let txt;
  if (!s.enabled) txt = "Deaktiviert";
  else if (s.status === "error") txt = `Fehler: ${s.error || "unbekannt"}`;
  else if (s.status === "checking") txt = "Prüfe Live-Status …";
  else if (s.status === "live_other") txt = "Live, aber kein GTA – wird nicht geclippt";
  else txt = "Offline · wartet auf Stream-Start";
  txt += ` · ${s.clips_24h || 0} Clip${s.clips_24h === 1 ? "" : "s"} in 24 h`;
  setText(el, "txt", txt).classList.toggle("err", s.status === "error");
}

async function loadLatest() {
  const clips = await api("GET", "/api/clips?status=all&limit=6");
  $("#latest-wrap").hidden = !clips.length;
  syncList($("#latest"), clips, clipCard, fillClipCard);
}

/* ------------------------------------------------------------------ Clips (Galerie) */
$$("#clip-filter button").forEach((b) => b.addEventListener("click", () => {
  state.clipStatus = b.dataset.status;
  $$("#clip-filter button").forEach((x) => x.classList.toggle("active", x === b));
  loadClips();
}));
$("#clip-streamer").addEventListener("change", (e) => { state.clipStreamer = e.target.value; loadClips(); });
$("#clip-sort").addEventListener("change", (e) => { state.clipSort = e.target.value; loadClips(); });

async function loadClips() {
  let clips = await api("GET", `/api/clips?status=${state.clipStatus}&limit=120${state.clipStreamer ? `&streamer_id=${state.clipStreamer}` : ""}`);
  if (state.clipSort === "score") clips = [...clips].sort((a, b) => (b.score || 0) - (a.score || 0));
  const sel = $("#clip-streamer");
  if (sel.options.length !== state.streamers.length + 1) {
    sel.innerHTML = `<option value="">Alle Streamer</option>` + state.streamers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    sel.value = state.clipStreamer;
  }
  syncList($("#gallery"), clips, clipCard, fillClipCard);
  $("#clips-empty").hidden = clips.length > 0;
  $("#clips-empty-h").textContent = EMPTY_CLIPS[state.clipStatus] || EMPTY_CLIPS.all;
}

function clipCard(c) {
  const el = document.createElement("div");
  el.className = "tc";
  el.addEventListener("click", () => openClip(Number(el.dataset.id)));
  return el;
}

function clipState(c) {
  const ups = c.uploads || [];
  if (c.status === "queued" || c.status === "processing") return `<span class="state work">⚙ ${esc(c.step || CLIP_STATUS[c.status])}</span>`;
  if (c.status === "failed") return `<span class="state fail">✗ Fehler</span>`;
  if (ups.some((u) => u.status === "uploading" || u.status === "pending")) return `<span class="state work">⬆ lädt hoch …</span>`;
  const done = [...new Set(ups.filter((u) => u.status === "done").map((u) => u.platform))];
  if (done.length) return `<span class="state up">${done.map((p) => `<span class="pf ${p}">${PF_SHORT[p] || esc(p)}</span>`).join("")} hochgeladen</span>`;
  if (ups.some((u) => u.status === "failed")) return `<span class="state fail">✗ Upload fehlgeschlagen</span>`;
  return `<span class="state">● Bereit</span>`;
}

function fillClipCard(el, c) {
  const sig = JSON.stringify([c.status, c.thumb_url, c.step, c.title, c.score, (c.uploads || []).map((u) => u.status)]);
  el.classList.toggle("sel", state.editing === c.id);
  if (el._sig === sig) return;
  el._sig = sig;
  const work = c.status === "queued" || c.status === "processing";
  const st = streamerById(c.streamer_id) || {};
  el.innerHTML = `${c.thumb_url ? `<img src="${c.thumb_url}" alt="" loading="lazy">` : scene(c.id)}
    ${c.thumb_url ? "" : `<div class="center">${work ? "Wird erstellt …" : esc(CLIP_STATUS[c.status] || "")}</div>`}
    <div class="t"><span class="score ${scoreClass(c.score)}">${Math.round(c.score || 0)}</span>${c.duration ? `<span class="dur">${fmtSec(c.duration)}</span>` : ""}</div>
    <div class="b"><b>${esc(c.title || CAT[c.category] || "Clip")}</b><span>${esc(st.name || "?")} · ${ago(c.created_at)}</span>${clipState(c)}</div>
    ${work ? `<div class="progress"><div></div></div>` : ""}`;
}

/* ------------------------------------------------------------------ Clip-Editor */
$("#drawer-bg").addEventListener("click", () => closeDrawer());

async function openClip(id) {
  let c;
  try { c = await api("GET", `/api/clips/${id}`); } catch (e) { return toast(e.message, true); }
  try { state.accounts = await api("GET", "/api/accounts"); } catch (_) { /* ignore */ }
  state.editing = id;
  renderDrawer(c);
  document.body.classList.add("drawer-open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $$(".tc").forEach((t) => t.classList.toggle("sel", Number(t.dataset.id) === id));
}

function closeDrawer() {
  if (!state.editing) return;
  state.editing = null;
  document.body.classList.remove("drawer-open");
  $("#drawer").setAttribute("aria-hidden", "true");
  const v = $("#drawer video");
  if (v) v.pause();
  $$(".tc.sel").forEach((t) => t.classList.remove("sel"));
}

function videoHtml(c) {
  if (c.video_url) return `<video src="${c.video_url}" poster="${c.thumb_url || ""}" controls preload="metadata" playsinline></video>`;
  const work = c.status === "queued" || c.status === "processing";
  return `<div class="vph">${scene(c.id)}<div class="center">${work ? `⚙ ${esc(c.step || "Wird erstellt …")}` : esc(CLIP_STATUS[c.status] || "")}</div></div>`;
}

function metaHtml(c) {
  const st = streamerById(c.streamer_id) || {};
  const bd = c.breakdown || {};
  const stCls = { ready: "color:var(--ok)", failed: "color:var(--err)", uploaded: "color:var(--text)" }[c.status] || "color:var(--warn)";
  return `<span class="score ${scoreClass(c.score)}">${Math.round(c.score || 0)}</span>
    ${c.llm_score ? `<span class="tag" title="${esc(bd.llm_reason || "")}">KI ${c.llm_score}/10</span>` : ""}
    <span class="tag">${CAT[c.category] || esc(c.category || "")}</span><span class="tag">${esc(st.name || "?")}</span>
    <span class="tag">${ago(c.created_at)}</span>${c.duration ? `<span class="tag">${fmtSec(c.duration)}</span>` : ""}
    ${c.source === "manual" ? `<span class="tag">manuell</span>` : c.source === "vod" ? `<span class="tag">VOD</span>` : ""}
    <span class="tag" style="${stCls}">● ${esc(CLIP_STATUS[c.status] || c.status)}${c.step ? ` · ${esc(c.step)}` : ""}</span>`;
}

function uploadsHtml(ups) {
  return (ups || []).map((u) => {
    const link = u.url && u.url.startsWith("http") ? `<a href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.url)}</a>` : esc(u.url || "");
    const label = { pending: "wartet", uploading: "lädt hoch …", done: "✓ hochgeladen", failed: "✗ fehlgeschlagen" }[u.status] || esc(u.status);
    return `<div><span class="pf ${esc(u.platform)}">${PF_SHORT[u.platform] || esc(u.platform)}</span><b>${esc(u.account_name)}</b>
      <span class="st-${esc(u.status)}">${label}</span>${link}${u.error ? `<span class="st-failed">${esc(u.error)}</span>` : ""}</div>`;
  }).join("");
}

function renderDrawer(c) {
  const d = $("#drawer");
  const st = streamerById(c.streamer_id) || {};
  const targets = st.upload_targets && st.upload_targets.length ? st.upload_targets : state.accounts.map((a) => a.id);
  const bd = c.breakdown || {};
  const bar = (label, v) => `<span>${label}</span><div class="b"><div style="width:${Math.max(0, Math.min(100, (v || 0) * 10))}%"></div></div><span>${Number(v || 0).toFixed(1).replace(".", ",")}</span>`;
  d._clip = c;
  d.innerHTML = `
    <div class="vid"><div data-k="video">${videoHtml(c)}</div>
      <a class="btn sm" data-k="dl" href="${c.video_url || "#"}" download ${c.video_url ? "" : "hidden"}>${icon("down", "xs")}Als MP4 speichern</a></div>
    <div class="form">
      <button class="close" type="button" data-act="close" title="Schließen (Esc)">${icon("x", "xs")}</button>
      <h3>Clip #${c.id}</h3>
      <div class="meta" data-k="meta">${metaHtml(c)}</div>
      <div data-k="err">${c.error ? `<div class="error-box">${esc(c.error)}</div>` : ""}</div>
      <label class="f">Titel <span class="saved" data-k="saved">✓ gespeichert</span></label><input type="text" data-f="title" value="${esc(c.title)}">
      <label class="f">Hook <em>– Einblendung am Anfang, nach Änderung „Neu rendern“</em></label><input type="text" data-f="hook" value="${esc(c.hook)}">
      <label class="f">Caption</label><textarea data-f="caption" rows="4">${esc(c.caption)}</textarea>
      <label class="f">Hashtags</label><input type="text" data-f="hashtags" value="${esc((c.hashtags || []).join(" "))}">
      <label class="f">Hochladen auf</label>
      <div class="targets">${state.accounts.length ? state.accounts.map((a) => `<label class="tg"><input type="checkbox" value="${a.id}" ${targets.includes(a.id) ? "checked" : ""}><span class="ck"></span><span class="pf ${esc(a.platform)}">${PF_SHORT[a.platform] || esc(a.platform)}</span>${esc(a.name)}</label>`).join("")
        : `<span class="small muted">Noch keine Accounts verbunden – <a href="#accounts" data-go="accounts">jetzt verbinden</a>.</span>`}</div>
      <div class="upload-row">
        <button class="btn primary big" data-act="upload">${icon("up")}<span data-k="uplabel"></span></button>
        <select data-k="layout" title="Layout">${LAYOUTS.map(([v, l]) => `<option value="${v}" ${(c.layout_override || "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        <button class="btn" data-act="rerender">${icon("redo")}Neu rendern</button>
        <button class="btn danger" data-act="discard">${icon("trash")}Verwerfen</button>
      </div>
      <div class="uploads" data-k="uploads">${uploadsHtml(c.uploads)}</div>
      <div class="why"><h4>Warum dieser Clip?</h4>
        <div class="bars">${bar("Chat-Tempo", bd.chat_rate)}${bar("Emotes", bd.emotes)}${bar("Chatter", bd.chatters)}${bar("Lautstärke", bd.audio)}${bar("Bewegung", bd.motion)}</div>
        <p>${bd.clip_requests ? `„Clip“-Rufe im Chat: ${bd.clip_requests} · ` : ""}Auslöser: ${bd.mode === "stream" ? "Stream (Ton/Bild)" : c.source === "manual" ? "von Hand" : "Chat"} · Chat-Lag ${bd.lag ?? "–"} s${bd.vod_time ? ` · VOD-Zeit ${fmtSec(bd.vod_time)}` : ""}</p>
        ${(c.chat_sample || []).length ? `<div class="chat">${c.chat_sample.map(([t, n]) => `<span>${n}× ${esc(t)}</span>`).join("")}</div>` : ""}
        ${c.transcript ? `<p>${esc(c.transcript)}</p>` : ""}
      </div>
    </div>`;
  updateUploadButton();
  d.querySelector('[data-act="close"]').addEventListener("click", closeDrawer);
  $$(".targets input", d).forEach((i) => i.addEventListener("change", updateUploadButton));
  $$("[data-f]", d).forEach((i) => i.addEventListener("change", () => saveClip(c.id).catch((e) => toast(e.message, true))));
  d.querySelector('[data-act="upload"]').addEventListener("click", async () => {
    const ids = $$(".targets input:checked", d).map((i) => +i.value);
    if (!ids.length) return toast("Mindestens einen Account auswählen", true);
    try {
      await saveClip(c.id);
      await api("POST", `/api/clips/${c.id}/upload`, { account_ids: ids });
      toast("Upload gestartet");
      setTimeout(refreshDrawer, 1000);
    } catch (e) { toast(e.message, true); }
  });
  d.querySelector('[data-act="rerender"]').addEventListener("click", async () => {
    try {
      await saveClip(c.id);
      await api("POST", `/api/clips/${c.id}/rerender`, { layout: d.querySelector('[data-k="layout"]').value || null });
      toast("Wird neu gerendert …");
      refreshDrawer();
    } catch (e) { toast(e.message, true); }
  });
  d.querySelector('[data-act="discard"]').addEventListener("click", async () => {
    if (!confirm("Clip verwerfen und Dateien löschen?")) return;
    try { await api("DELETE", `/api/clips/${c.id}`); closeDrawer(); toast("Verworfen"); refresh(); } catch (e) { toast(e.message, true); }
  });
}

function updateUploadButton() {
  const d = $("#drawer"), c = d._clip;
  if (!c) return;
  const n = $$(".targets input:checked", d).length;
  const btn = d.querySelector('[data-act="upload"]');
  btn.disabled = !c.video_url || !n;
  setText(d, "uplabel", !c.video_url ? "Erst nach dem Rendern" : !state.accounts.length ? "Erst Account verbinden"
    : n ? `Auf ${n} Account${n > 1 ? "s" : ""} hochladen` : "Account auswählen");
}

async function saveClip(id) {
  const d = $("#drawer");
  const v = (f) => d.querySelector(`[data-f="${f}"]`).value;
  await api("PATCH", `/api/clips/${id}`, { title: v("title"), hook: v("hook"), caption: v("caption"),
    hashtags: v("hashtags").split(/\s+/).filter(Boolean).map((h) => (h.startsWith("#") ? h : "#" + h)) });
  const s = d.querySelector('[data-k="saved"]');
  s.classList.add("show");
  clearTimeout(s._h);
  s._h = setTimeout(() => s.classList.remove("show"), 1800);
}

/** Status, Video und Uploads im offenen Editor nachziehen – Eingabefelder bleiben unangetastet. */
async function refreshDrawer() {
  if (!state.editing) return;
  const id = state.editing;
  let c;
  try { c = await api("GET", `/api/clips/${id}`); } catch (_) { return; }
  const d = $("#drawer");
  if (state.editing !== id || !d._clip) return;
  const old = d._clip;
  d._clip = c;
  d.querySelector('[data-k="meta"]').innerHTML = metaHtml(c);
  d.querySelector('[data-k="err"]').innerHTML = c.error ? `<div class="error-box">${esc(c.error)}</div>` : "";
  d.querySelector('[data-k="uploads"]').innerHTML = uploadsHtml(c.uploads);
  if (c.video_url !== old.video_url || (!c.video_url && c.step !== old.step)) {
    d.querySelector('[data-k="video"]').innerHTML = videoHtml(c);
    const dl = d.querySelector('[data-k="dl"]');
    dl.hidden = !c.video_url;
    dl.href = c.video_url || "#";
  }
  updateUploadButton();
}

/* ------------------------------------------------------------------ Streamer */
$("#add-streamer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const s = await api("POST", "/api/streamers", { url: f.get("url"), name: f.get("name"), language: f.get("language") });
    toast(`${s.name} hinzugefügt (${s.platform}) – wird jetzt überwacht`);
    e.target.reset();
    await loadStreamers(true);
  } catch (err) { toast(err.message, true); }
});

async function refreshStreamerCache() {
  try { state.streamers = await api("GET", "/api/streamers"); } catch (_) { /* ignore */ }
}

async function loadStreamers(force = false) {
  const [streamers, accounts] = await Promise.all([api("GET", "/api/streamers"), api("GET", "/api/accounts")]);
  state.streamers = streamers;
  state.accounts = accounts;
  const list = $("#streamer-list");
  if (!force && list.querySelector(".dirty")) return;
  list.innerHTML = streamers.length ? "" : `<div class="empty-big"><div class="ico">${icon("users")}</div><h2>Noch keine Streamer</h2>
    <p>Oben einen Twitch-, YouTube- oder Kick-Link einfügen. Der Clipper prüft jede Minute, ob jemand live ist.</p></div>`;
  for (const s of streamers) list.appendChild(renderStreamer(s));
}

function statusPill(id) {
  const s = state.status ? state.status.streamers.find((x) => x.id === id) : null;
  if (!s) return "";
  if (s.status === "live") return `<span class="pill live">● LIVE</span>`;
  if (s.status === "paused" || s.status === "live_other") return `<span class="pill paused">LIVE · kein GTA</span>`;
  if (s.status === "error") return `<span class="pill error" title="${esc(s.error || "")}">Fehler</span>`;
  return `<span class="tag">${s.enabled === false ? "Deaktiviert" : "Offline"}</span>`;
}

function renderStreamer(s) {
  const el = document.createElement("div");
  el.className = "sr";
  const set = s.settings || {};
  const det = set.detection || {};
  const fc = s.facecam === "none" ? "keine" : s.facecam ? "eingezeichnet" : "automatisch";
  const targets = s.upload_targets || [];
  const opt = (list, cur) => list.map(([v, l]) => `<option value="${v}" ${(cur ?? "") === v ? "selected" : ""}>${l}</option>`).join("");
  el.innerHTML = `
    <div class="avatar ${esc(s.platform)}">${esc((s.name || "?")[0])}</div>
    <div>
      <div class="nm"><b>${esc(s.name)}</b><span class="pf ${esc(s.platform)}">${esc(s.platform)}</span>${statusPill(s.id)}</div>
      <a class="url" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a>
      <div class="set">
        <label><span>Auslöse-Score</span><input type="number" min="40" max="99" data-s="detection.trigger_score" value="${det.trigger_score ?? ""}" placeholder="72"></label>
        <label><span>Max. Clips/Std</span><input type="number" min="1" max="60" data-s="detection.max_clips_per_hour" value="${det.max_clips_per_hour ?? ""}" placeholder="10"></label>
        <label><span>Max. Länge (s)</span><input type="number" min="15" max="180" data-s="clips.max_seconds" value="${(set.clips || {}).max_seconds ?? ""}" placeholder="60"></label>
        <label title="Sprache der Untertitel. Bei Englisch sind auch Titel, Hook und Hashtags englisch."><span>Sprache</span><select data-s="transcription.language">${opt([["", "Standard"], ["de", "Deutsch"], ["en", "Englisch"], ["auto", "Automatisch"], ["tr", "Türkisch"], ["ar", "Arabisch"]], (set.transcription || {}).language)}</select></label>
        <label><span>Layout</span><select data-s="render.layout">${opt([["", "Auto"], ["stack", "Facecam oben"], ["crop", "Nur Gameplay"], ["blur", "Ganzes Bild + Blur"]], (set.render || {}).layout)}</select></label>
        <label><span>Facecam: ${fc}</span><button type="button" class="btn sm" data-act="facecam">${icon("cam", "xs")}Einzeichnen …</button></label>
      </div>
      <div class="tgt"><span>Standard-Upload-Ziele</span><div class="targets">${state.accounts.length
        ? state.accounts.map((a) => `<label class="tg"><input type="checkbox" value="${a.id}" ${targets.includes(a.id) ? "checked" : ""}><span class="ck"></span><span class="pf ${esc(a.platform)}">${PF_SHORT[a.platform] || esc(a.platform)}</span>${esc(a.name)}</label>`).join("")
        : `<span class="small muted">Noch keine Accounts – ohne Auswahl sind beim Upload alle vorausgewählt.</span>`}</div></div>
    </div>
    <div class="side">
      <label class="switch"><input type="checkbox" data-act="enabled" ${s.enabled ? "checked" : ""}><span class="sw"></span>Aktiv</label>
      <button class="btn primary sm" data-act="save" hidden>Speichern</button>
      <button class="btn sm danger" data-act="delete">${icon("trash", "xs")}Entfernen</button>
    </div>`;
  $$("input, select", el).forEach((i) => i.addEventListener("change", () => {
    el.classList.add("dirty");
    el.querySelector('[data-act="save"]').hidden = false;
  }));
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
      el.classList.remove("dirty");
      toast("Gespeichert");
      loadStreamers();
    } catch (e) { toast(e.message, true); }
  });
  el.querySelector('[data-act="delete"]').addEventListener("click", async () => {
    if (!confirm(`${s.name} entfernen? (Clips bleiben erhalten)`)) return;
    try { await api("DELETE", `/api/streamers/${s.id}`); loadStreamers(true); } catch (e) { toast(e.message, true); }
  });
  el.querySelector('[data-act="facecam"]').addEventListener("click", () => openFacecam(s));
  return el;
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
  ctx.fillStyle = "rgba(0,0,0,.5)"; ctx.fillRect(0, 0, c.width, c.height);
  ctx.clearRect(x, y, w, h);
  ctx.strokeStyle = "#ff3d7f"; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
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
    $("#facecam-dlg").close();
    toast("Facecam gespeichert – gilt für neue Clips (alte: Neu rendern)");
    await refreshStreamerCache();
    if (state.tab === "streamers") loadStreamers(true);
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
  $$(".conns").forEach((box) => {
    const p = box.dataset.p;
    box.innerHTML = accounts.filter((a) => a.platform === p).map((a) => `
      <div class="conn"><div class="avatar ${p}">${esc((a.name || "?")[0])}</div>
        <div><b>${esc(a.name)}</b><span>● verbunden ${ago(a.created_at)}</span></div>
        <button data-del="${a.id}" title="Account trennen">${icon("x", "xs")}</button></div>`).join("");
  });
  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Account trennen?")) return;
    try { await api("DELETE", `/api/accounts/${b.dataset.del}`); loadAccounts(); } catch (e) { toast(e.message, true); }
  }));
}
$$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
  const text = b.dataset.copy;
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Befehl kopiert – im Terminal im Projektordner einfügen");
}));
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

/* ------------------------------------------------------------------ Start + Aktualisierung */
let ticks = 0;
async function tick() {
  ticks++;
  try { await loadStatus(); } catch (_) { return; }
  try {
    if (state.tab === "live") { renderLive(); if (ticks % 5 === 0) await loadLatest(); }
    if (state.tab === "clips" && ticks % 3 === 0) await loadClips();
    if (state.editing && ticks % 2 === 0) await refreshDrawer();
    if (ticks % 15 === 0) await refreshStreamerCache();
  } catch (e) { console.error(e); }
}

(async () => {
  await Promise.all([loadInfo(), refreshStreamerCache(), loadStatus().catch(() => {})]);
  const initial = location.hash.slice(1);
  showTab(TABS.includes(initial) ? initial : "live");
  setInterval(tick, 2000);
})();
