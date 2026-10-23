// Second Look: the screen of a simulated Alexa+ device.
// This page is a real MCP client (Streamable HTTP, protocol 2025-11-25) for the crv-guide
// server: it renders state and handles the on-screen buttons itself. Spoken requests go to
// the Strands agent (/api/turn), which calls the same MCP server with its own connection.

const $ = (id) => document.getElementById(id);
const convoId = (() => {
  try { return localStorage.getItem("sl.convo") || (localStorage.setItem("sl.convo", crypto.randomUUID()), localStorage.getItem("sl.convo")); }
  catch { return crypto.randomUUID(); }
})();

// ---------------------------------------------------------------- MCP client

class McpClient {
  constructor(url) { this.url = url; this.sid = null; this.version = null; this.id = 0; }

  async post(msg) {
    const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
    if (this.sid) headers["Mcp-Session-Id"] = this.sid;
    if (this.version) headers["MCP-Protocol-Version"] = this.version;
    const res = await fetch(this.url, { method: "POST", headers, body: JSON.stringify(msg) });
    const sid = res.headers.get("mcp-session-id");
    if (sid) this.sid = sid;
    if (res.status === 202 || msg.id === undefined) return null;
    if (res.status === 404 && this.sid) { this.sid = null; this.version = null; throw new Error("session-expired"); }
    const type = res.headers.get("content-type") || "";
    if (type.includes("application/json")) return res.json();
    const text = await res.text(); // text/event-stream: find the response for our id
    for (const block of text.split(/\n\n/)) {
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
      if (!data) continue;
      const m = JSON.parse(data);
      if (m.id === msg.id) return m;
    }
    throw new Error("no response for request " + msg.id);
  }

  async initialize() {
    const r = await this.post({
      jsonrpc: "2.0", id: ++this.id, method: "initialize",
      params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "second-look-screen", version: "0.1.0" } },
    });
    this.version = r.result.protocolVersion;
    await this.post({ jsonrpc: "2.0", method: "notifications/initialized" });
    return r.result;
  }

  async call(name, args = {}) {
    let r;
    try {
      r = await this.post({ jsonrpc: "2.0", id: ++this.id, method: "tools/call", params: { name, arguments: args } });
    } catch (e) {
      if (String(e.message) !== "session-expired") throw e;
      await this.initialize();
      r = await this.post({ jsonrpc: "2.0", id: ++this.id, method: "tools/call", params: { name, arguments: args } });
    }
    if (r.error) throw new Error(r.error.message);
    return r.result;
  }
}

let mcp, mcpBase, snap = null;

// ---------------------------------------------------------------- rendering

function render(s) {
  if (!s || !s.step) return;
  snap = s;
  $("emptyState").hidden = true;
  $("stepCard").hidden = false;
  const st = s.step;
  $("stepNo").textContent = `Step ${st.step_number} of ${st.total_steps}`;
  $("guideTitle").textContent = s.guide_title;
  $("stepTitle").textContent = st.title;
  $("stepSay").textContent = st.say;
  $("stepLook").textContent = st.look_for;
  const seen = $("stepSeen");
  seen.hidden = !st.visual_note;
  seen.querySelector("span:last-child").textContent = st.visual_note || "";
  if ($("frameImg").src !== st.frame_url) $("frameImg").src = st.frame_url;
  $("frameTime").textContent = `from the video at ${st.frame_time}`;
  $("replayBtn").onclick = () => playClip(st.clip.video_url, st.clip.start_s, st.clip.end_s, `${st.title} · video ${st.video_time}`);
  const prog = $("progress");
  prog.innerHTML = "";
  for (let i = 1; i <= st.total_steps; i++) {
    const d = document.createElement("i");
    if ((s.completed_steps || []).includes(i)) d.className = "done";
    if (i === st.step_number) d.className = "now";
    prog.appendChild(d);
  }
  $("nextBox").hidden = !s.next_step_title;
  $("nextTitle").textContent = s.next_step_title || "";
  for (const b of ["backBtn", "repeatBtn", "nextBtn"]) $(b).disabled = false;
  renderTimers(s.timers || []);
}

let timers = [];
const alerted = new Set();
function renderTimers(list) {
  timers = list.map((t) => ({ ...t, due: Date.parse(t.due_at_utc) }));
  tickTimers();
}
function fmt(sec) { sec = Math.max(0, Math.round(sec)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`; }
function tickTimers() {
  const box = $("timerChips");
  box.innerHTML = "";
  for (const t of timers) {
    const left = (t.due - Date.now()) / 1000;
    const el = document.createElement("span");
    el.className = "chip" + (left <= 0 ? " done" : "");
    el.textContent = `⏱ ${t.label} ${left <= 0 ? "done" : fmt(left)}`;
    box.appendChild(el);
    if (left <= 0 && !alerted.has(t.timer_id) && Date.now() - t.due < 60 * 60 * 1000) {
      alerted.add(t.timer_id);
      ring(`${t.label}: time's up.`);
    }
  }
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
setInterval(tickTimers, 1000);

function ring(text) {
  $("alarmText").textContent = text;
  $("alarm").hidden = false;
  try {
    const ctx = new AudioContext();
    [0, 0.35, 0.7].forEach((d) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.18, ctx.currentTime + d); g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + d + 0.3);
      o.start(ctx.currentTime + d); o.stop(ctx.currentTime + d + 0.32);
    });
  } catch {}
  speak(text);
}
$("alarmOk").onclick = () => { $("alarm").hidden = true; };

function showEvidence(data) {
  const box = $("evidence");
  box.innerHTML = "";
  for (const e of (data.evidence || []).slice(0, 3)) {
    const el = document.createElement("div");
    el.className = "ev";
    el.innerHTML = `<img alt=""><div><div class="t"></div><div class="q"></div></div>`;
    el.querySelector("img").src = e.frame_url;
    const when = e.said_at ? `said at ${e.said_at}` : `video ${e.video_time}`;
    el.querySelector(".t").textContent = e.step_number ? `step ${e.step_number} · ${when}` : when;
    el.querySelector(".q").textContent = e.quote && !e.step_number ? `“${e.quote}”` : e.text;
    const c = e.clip || { start_s: e.start_s, end_s: e.start_s + 12 };
    el.onclick = () => snap && playClip(snap.step.clip.video_url, c.start_s, c.end_s, `From the video at ${e.said_at || e.video_time}`);
    box.appendChild(el);
  }
}

function showMoment(data) {
  $("moment").hidden = false;
  const clip = $("clip");
  clip.pause(); clip.hidden = true;
  const img = $("momentImg");
  img.hidden = false; img.src = data.image_url;
  $("momentCap").textContent = `From the video at ${data.video_time}`;
  clip.dataset.src = data.clip.video_url; clip.dataset.start = data.clip.start_s; clip.dataset.end = data.clip.end_s;
}

function playClip(url, start, end, caption) {
  $("moment").hidden = false;
  $("momentImg").hidden = true;
  const v = $("clip");
  v.hidden = false;
  if (!v.src.endsWith(url)) v.src = url;
  $("momentCap").textContent = caption || "";
  const go = () => { v.currentTime = start; v.play().catch(() => {}); };
  if (v.readyState >= 1) go(); else v.addEventListener("loadedmetadata", go, { once: true });
  v.ontimeupdate = () => { if (v.currentTime >= end) v.pause(); };
}
$("momentClose").onclick = () => { $("clip").pause(); $("moment").hidden = true; };
$("momentImg").onclick = () => { const c = $("clip"); playClip(c.dataset.src, +c.dataset.start, +c.dataset.end, $("momentCap").textContent); };

// ---------------------------------------------------------------- voice

let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en-US"));
  voice = vs.find((v) => /Samantha|Google US English|Ava|Allison/.test(v.name)) || vs[0] || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

function setLight(state) { $("lightbar").className = "lightbar" + (state ? " " + state : ""); }

const REC = new URLSearchParams(location.search).has("rec");
function speak(text) {
  if (REC || !("speechSynthesis" in window) || !text) return Promise.resolve();
  stopListening();
  speechSynthesis.cancel();
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.03;
    u.onstart = () => setLight("speak");
    u.onend = u.onerror = () => { setLight(""); resolve(); };
    speechSynthesis.speak(u);
  });
}

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false;
function startListening() {
  if (!SR) { $("typeInput").focus(); return; }
  speechSynthesis.cancel();
  rec = new SR();
  rec.lang = "en-US"; rec.interimResults = true; rec.continuous = false;
  let finalText = "";
  rec.onresult = (ev) => {
    let interim = "";
    for (const r of ev.results) { if (r.isFinal) finalText = r[0].transcript; else interim += r[0].transcript; }
    $("youSaid").textContent = finalText || interim;
  };
  rec.onend = () => { listening = false; $("micBtn").classList.remove("on"); setLight(""); if (finalText.trim()) ask(finalText); };
  rec.onerror = () => { listening = false; $("micBtn").classList.remove("on"); setLight(""); };
  rec.start(); listening = true;
  $("micBtn").classList.add("on"); setLight("listen");
}
function stopListening() { if (rec && listening) { try { rec.stop(); } catch {} } }
$("micBtn").onclick = () => (listening ? stopListening() : startListening());
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); listening ? stopListening() : startListening(); }
});

// ---------------------------------------------------------------- talking to the agent

async function ask(text) {
  text = text.replace(/^\s*(hey\s+)?alexa[,.!]?\s*/i, "").trim();
  if (!text) return;
  $("youSaid").textContent = text;
  $("alexaSaid").textContent = "…";
  $("evidence").innerHTML = "";
  setLight("think");
  let out;
  try {
    const res = await fetch("/api/turn", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ convo_id: convoId, text }) });
    out = await res.json();
  } catch (e) {
    out = { reply: "I lost the connection. Try again.", events: [] };
  }
  setLight("");
  for (const ev of out.events || []) {
    const d = ev.data || {};
    if (d.session_id && d.step) render(d);
    if (ev.tool === "ask_source") showEvidence(d);
    if (ev.tool === "show_moment" && d.image_url) showMoment(d);
    if (ev.tool === "set_timer" && d.timers) renderTimers(d.timers);
    if (ev.tool === "cancel_timer" && d.timers) renderTimers(d.timers);
  }
  $("alexaSaid").textContent = out.reply || "";
  await refresh();
  await speak(out.reply);
}
$("typeForm").onsubmit = (e) => { e.preventDefault(); const t = $("typeInput").value; $("typeInput").value = ""; ask(t); };

// ---------------------------------------------------------------- direct MCP actions (buttons)

async function refresh() {
  if (!mcp) return;
  try {
    const r = await mcp.call("get_session", snap ? { session_id: snap.session_id } : {});
    if (!r.isError) render(r.structuredContent);
  } catch {}
}

async function move(action) {
  if (!snap) return;
  const r = await mcp.call("move_step", { session_id: snap.session_id, action, expected_revision: snap.revision });
  if (!r.isError) {
    render(r.structuredContent);
    $("youSaid").textContent = action === "back" ? "(tapped Back)" : action === "repeat" ? "(tapped Repeat)" : "(tapped Next)";
    $("alexaSaid").textContent = r.structuredContent.step.say;
    $("evidence").innerHTML = "";
    speak(r.structuredContent.step.say);
  }
}
$("backBtn").onclick = () => move("back");
$("repeatBtn").onclick = () => move("repeat");
$("nextBtn").onclick = () => move("next");

async function importAndStart(source) {
  const imp = $("importing");
  imp.hidden = false; imp.textContent = "Watching the video…";
  let r = await mcp.call("import_video", { source });
  const job = r.structuredContent.job_id;
  const t0 = Date.now();
  for (;;) {
    r = await mcp.call("get_import", { job_id: job });
    const s = r.structuredContent;
    if (s.status === "ready") break;
    if (s.status === "failed") { imp.textContent = "Couldn't watch that video: " + (s.error || ""); return; }
    imp.textContent = `${s.stage[0].toUpperCase()}${s.stage.slice(1)}… ${Math.round((Date.now() - t0) / 1000)} s`;
    await new Promise((res) => setTimeout(res, 1500));
  }
  const g = r.structuredContent.guide;
  imp.textContent = `Ready: ${g.title} (${g.steps.length} steps).`;
  const s = await mcp.call("start_session", { guide_id: g.guide_id });
  render(s.structuredContent);
  $("alexaSaid").textContent = `I watched the video and split it into ${g.steps.length} steps. Say next when you're ready.`;
  speak($("alexaSaid").textContent);
}
$("loadSample").onclick = () => importAndStart("sample:pasta");
$("urlForm").onsubmit = (e) => { e.preventDefault(); const u = $("urlInput").value.trim(); if (u) importAndStart(u); };

// ---------------------------------------------------------------- MCP trace drawer

let traceSince = 0, traceTimer = null;
const clientBySession = {};
async function pollTrace() {
  try {
    const rows = await (await fetch(`${mcpBase}/trace?since=${traceSince}`)).json();
    const box = $("traceRows");
    for (const r of rows) {
      traceSince = Math.max(traceSince, r.ts);
      if (r.method === "initialize" && r.client) clientBySession["pending:" + r.client] = true;
      if (r.client && r.session) clientBySession[r.session] = r.client;
      const who = r.client || clientBySession[r.session] || "";
      const el = document.createElement("div");
      const isRes = r.direction && r.direction.startsWith("server");
      el.className = "tr" + (isRes ? " res" : "");
      const t = new Date(r.ts * 1000).toLocaleTimeString([], { hour12: false });
      let main = r.method + (r.tool ? `  ${r.tool}` : "");
      let detail = [];
      if (r.requested_protocol_version) detail.push(`requests protocol ${r.requested_protocol_version}`);
      if (r.negotiated_protocol_version) detail.push(`negotiated protocol ${r.negotiated_protocol_version} · server ${r.server}`);
      if (r.header_protocol_version) detail.push(`MCP-Protocol-Version: ${r.header_protocol_version}`);
      if (r.http_status) detail.push(`HTTP ${r.http_status}`);
      el.innerHTML = `<div class="m"></div><div class="d"></div><div class="a"></div>`;
      el.querySelector(".m").textContent = main;
      el.querySelector(".d").textContent = `${t} · ${r.direction || ""}${who ? " · " + who : ""}${r.session ? " · session " + r.session : ""}`;
      el.querySelector(".a").textContent = [detail.join(" · "), r.arguments ? JSON.stringify(r.arguments) : ""].filter(Boolean).join("\n");
      box.appendChild(el);
    }
    if (rows.length) box.scrollTop = box.scrollHeight;
  } catch {}
}
$("traceBtn").onclick = () => { $("trace").hidden = false; pollTrace(); traceTimer = traceTimer || setInterval(pollTrace, 1000); };
$("traceClose").onclick = () => { $("trace").hidden = true; clearInterval(traceTimer); traceTimer = null; };

// ---------------------------------------------------------------- boot

(async function boot() {
  const cfg = await (await fetch("/api/config")).json();
  mcpBase = cfg.mcp_base;
  mcp = new McpClient(cfg.mcp_url);
  try {
    await mcp.initialize();
  } catch (e) {
    $("alexaSaid").textContent = "Can't reach the crv-guide MCP server. Is it running?";
    return;
  }
  const r = await mcp.call("get_session", {});
  if (!r.isError) {
    render(r.structuredContent);
    $("alexaSaid").textContent = `Welcome back. You're on step ${r.structuredContent.step.step_number}: ${r.structuredContent.step.title}.`;
  }
})();

// ---------------------------------------------------------------- demo recording hooks
// Used only by the demo-video script: plays a spoken request through the same path as the
// microphone (listening light, live transcript, then ask()), and logs timings for the soundtrack.
window.__log = [];
const _logEvt = (type, text) => window.__log.push({ type, text, t: Date.now() });
window.__say = async (text, ms = 1600) => {
  _logEvt("user", text);
  setLight("listen"); $("micBtn").classList.add("on");
  $("evidence").innerHTML = "";
  const words = text.split(" ");
  for (let i = 1; i <= words.length; i++) {
    $("youSaid").textContent = words.slice(0, i).join(" ");
    await new Promise((r) => setTimeout(r, ms / words.length));
  }
  $("micBtn").classList.remove("on"); setLight("");
  await ask(text);
  _logEvt("reply", $("alexaSaid").textContent);
};
