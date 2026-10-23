// Second Look: a simulated Alexa+ host (Echo Show class screen).
// It is two MCP things at once, the way Alexa+ is:
//  - an MCP client of the crv-guide server (Streamable HTTP, protocol 2025-11-25), and
//  - an MCP Apps host: the add-on's own UI is the server's ui:// resource, shown in a sandboxed
//    iframe and driven over JSON-RPC postMessage (ui/initialize, ui/notifications/tool-result,
//    tools/call proxied to the server).
// Spoken requests go to the Strands agent (/api/turn), which calls the same server; every tool
// result from that turn is forwarded to the add-on view.

const $ = (id) => document.getElementById(id);
const REC = new URLSearchParams(location.search).has("rec");
const convoId = (() => {
  try { return localStorage.getItem("sl.convo") || (localStorage.setItem("sl.convo", crypto.randomUUID()), localStorage.getItem("sl.convo")); }
  catch { return crypto.randomUUID(); }
})();

// ---------------------------------------------------------------- fit the device to the window

function fit() {
  const s = Math.min((innerWidth - 40) / 1320, (innerHeight - 40) / 840);
  $("device").style.transform = `scale(${s})`;
}
addEventListener("resize", fit);
fit();

// ---------------------------------------------------------------- MCP client (host -> server)

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
      params: {
        protocolVersion: "2025-11-25",
        // MCP Apps: this host can render ui:// resources
        capabilities: { extensions: { "io.modelcontextprotocol/ui": { mimeTypes: ["text/html;profile=mcp-app"] } } },
        clientInfo: { name: "alexa-plus-simulator", version: "0.2.0" },
      },
    });
    this.version = r.result.protocolVersion;
    await this.post({ jsonrpc: "2.0", method: "notifications/initialized" });
    return r.result;
  }

  async rpc(method, params) {
    let r;
    try {
      r = await this.post({ jsonrpc: "2.0", id: ++this.id, method, params });
    } catch (e) {
      if (String(e.message) !== "session-expired") throw e;
      await this.initialize();
      r = await this.post({ jsonrpc: "2.0", id: ++this.id, method, params });
    }
    if (r.error) throw new Error(r.error.message);
    return r.result;
  }
  call(name, args = {}) { return this.rpc("tools/call", { name, arguments: args }); }
}

let mcp, mcpBase, snap = null;

// ---------------------------------------------------------------- MCP Apps host (host <-> add-on view)

const view = { frame: null, ready: false, queue: [] };
const uiTools = new Set();

async function mountView() {
  if (view.frame) { view.frame.hidden = false; $("launcher").hidden = true; return; }  // back from "New video"
  const { tools } = await mcp.rpc("tools/list", {});
  const bound = tools.filter((t) => t._meta && t._meta.ui && t._meta.ui.resourceUri);
  bound.forEach((t) => uiTools.add(t.name));
  if (!bound.length) return;
  const uri = bound[0]._meta.ui.resourceUri;
  const res = await mcp.rpc("resources/read", { uri });
  const doc = res.contents.find((c) => (c.mimeType || "").startsWith("text/html"));
  trace({ method: "resources/read", detail: `${uri} · ${doc.mimeType} · ${doc.text.length} bytes`, dir: "host → server" });
  const f = $("addon");
  f.srcdoc = withCsp(doc.text, (doc._meta && doc._meta.ui && doc._meta.ui.csp) || {});  // opaque origin: allow-scripts only
  f.hidden = false;
  $("launcher").hidden = true;
  view.frame = f;
}

// Enforce the CSP the resource declares (_meta.ui.csp), as MCP Apps hosts must: the view may load
// scripts, styles, images and media only from its declared resource domains, and may not connect anywhere.
function withCsp(html, csp) {
  const res = (csp.resourceDomains || []).join(" ");
  const conn = (csp.connectDomains || []).join(" ") || "'none'";
  const policy = [
    "default-src 'none'",
    `script-src 'unsafe-inline' ${res}`,
    `style-src 'unsafe-inline' ${res}`,
    `img-src data: ${res}`,
    `media-src ${res}`,
    `font-src data: ${res}`,
    `connect-src ${conn}`,
    "frame-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
  const meta = `<meta http-equiv="Content-Security-Policy" content="${policy}">`;
  return /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (h) => `${h}\n${meta}`) : meta + html;
}

function hostContext() {
  const r = $("canvas").getBoundingClientRect();
  const scale = $("device").getBoundingClientRect().width / 1320;
  return {
    theme: "dark",
    displayMode: "inline",
    availableDisplayModes: ["inline"],
    containerDimensions: { width: Math.round(r.width / scale), height: Math.round(r.height / scale) },
    platform: "web",
    deviceClass: "echo-show-15",   // Alexa+ hosts also report their device class
    deviceCapabilities: { touch: true, hover: false },
    locale: "en-US",
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    userAgent: "second-look alexa-plus-simulator/0.2.0",
    safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
  };
}

function postToView(msg) { view.frame.contentWindow.postMessage(msg, "*"); }
function toView(method, params) {
  const msg = { jsonrpc: "2.0", method, params };
  if (!view.ready) { view.queue.push(msg); return; }
  postToView(msg);
  trace({ method, detail: method.endsWith("tool-input") ? JSON.stringify(params.arguments || {}) : "", dir: "host → view", ui: true });
}
function reply(id, result) { postToView({ jsonrpc: "2.0", id, result }); }
function replyError(id, message, code = -32000) { postToView({ jsonrpc: "2.0", id, error: { code, message } }); }

window.addEventListener("message", async (e) => {
  if (!view.frame || e.source !== view.frame.contentWindow) return;
  const m = e.data;
  if (!m || m.jsonrpc !== "2.0" || !m.method) return;
  m.params = m.params || {};
  if (m.method === "notifications/message" && m.params.data && m.params.data.event === "playback") { onPlayback(m.params.data.state); return; }
  trace({ method: m.method, detail: m.method === "tools/call" ? `${m.params.name} ${JSON.stringify(m.params.arguments || {})}` : m.method === "ui/initialize" ? `protocol ${m.params.protocolVersion}` : "", dir: "view → host", ui: true });
  switch (m.method) {
    case "ui/initialize":
      reply(m.id, {
        protocolVersion: "2026-01-26",
        hostInfo: { name: "alexa-plus-simulator", version: "0.2.0" },
        hostCapabilities: { serverTools: {}, serverResources: {}, logging: {}, sandbox: { csp: { resourceDomains: [mcpBase, "https://unpkg.com"] } } },
        hostContext: hostContext(),
      });
      return;
    case "ui/notifications/initialized":
      view.ready = true;
      for (const q of view.queue.splice(0)) toView(q.method, q.params);
      return;
    case "tools/call":
      if (!uiTools.has(m.params.name)) { replyError(m.id, `The view may only call its own tools, not ${m.params.name}`, -32602); return; }
      try {
        const r = await mcp.call(m.params.name, m.params.arguments || {});
        reply(m.id, r);
        afterViewCall(m.params.name, r);
      } catch (err) { replyError(m.id, String(err.message || err)); }
      return;
    case "resources/read":
      try { reply(m.id, await mcp.rpc("resources/read", m.params)); } catch (err) { replyError(m.id, String(err.message || err)); }
      return;
    case "ui/request-display-mode":
      reply(m.id, { mode: "inline" });
      return;
    case "notifications/message":
      console.log("[add-on]", m.params);
      return;
    default:
      if (m.id !== undefined) replyError(m.id, `Method not found: ${m.method}`, -32601);
  }
});

// A tool result reaches the host: update the host's own state, then show it in the add-on view.
function onToolResult(name, args, result, { forward = true } = {}) {
  const d = (result && result.structuredContent) || {};
  if (d.session_id && d.step) snap = d;
  if (d.timers) renderTimers(d.timers);

  if (forward && uiTools.has(name) && view.frame) {
    toView("ui/notifications/tool-input", { arguments: args || {} });
    toView("ui/notifications/tool-result", result);
  }
}

// The view called a tool itself (its Back / Next buttons, an evidence card): Alexa says the result.
function afterViewCall(name, result) {
  onToolResult(name, {}, result, { forward: false });
  const d = (result && result.structuredContent) || {};
  if (name === "move_step" && d.speak) {
    $("youSaid").textContent = "(tapped on the screen)";
    $("alexaSaid").textContent = d.speak;
    speak(d.speak);
  }
}

// ---------------------------------------------------------------- timers (Alexa's own)

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
    el.innerHTML = `<i class="ph-bold ph-timer"></i><span></span>`;
    el.querySelector("span").textContent = `${t.label} ${left <= 0 ? "done" : fmt(left)}`;
    box.appendChild(el);
    if (left <= 0 && !alerted.has(t.timer_id) && Date.now() - t.due < 60 * 60 * 1000) {
      alerted.add(t.timer_id);
      ring(`${t.label}: time's up.`);
    }
  }
  $("clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
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

// ---------------------------------------------------------------- voice

let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en-US"));
  voice = vs.find((v) => /Samantha|Google US English|Ava|Allison/.test(v.name)) || vs[0] || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

function setLight(state) { $("lightbar").className = "lightbar" + (state ? " " + state : ""); }

function speak(text) {
  if (REC || !("speechSynthesis" in window) || !text) return Promise.resolve();
  stopListening();
  speechSynthesis.cancel();
  hfPause();
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.03;
    let done = false;
    const finish = () => { if (done) return; done = true; setLight(""); hfResume(); resolve(); };
    u.onstart = () => setLight("speak");
    u.onend = u.onerror = finish;
    setTimeout(finish, 1500 + text.length * 120);  // some voices never fire onend
    speechSynthesis.speak(u);
  });
}

// push-to-talk
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
$("micBtn").onclick = () => (handsFree ? setHandsFree(false) : listening ? stopListening() : startListening());
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); listening ? stopListening() : startListening(); }
});

// Hands-free mode: like a real Echo Show, keep listening and act only on sentences that start
// with "Alexa". Listening pauses while Alexa is talking, while the agent is working and while a
// clip plays, so the page never hears itself or the video, and re-arms afterwards.
let handsFree = false, hfRec = null, hfPaused = 0, hfAwaitUntil = 0;
const WAKE = /^\s*(?:hey\s+|ok(?:ay)?\s+)?alexa\b[\s,.!?]*(.*)$/i;
function hfStart() {
  if (!handsFree || hfPaused > 0 || hfRec) return;
  if (REC || !SR) { setLight("idle"); return; }  // demo recording: requests are typed through window.__say
  const r = new SR();
  r.lang = "en-US"; r.continuous = true; r.interimResults = true;
  r.onresult = (ev) => {
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const res = ev.results[i], text = res[0].transcript.trim();
      if (!res.isFinal) { if (WAKE.test(text) || Date.now() < hfAwaitUntil) $("youSaid").textContent = text; continue; }
      hfHeard(text);
    }
  };
  r.onend = () => { if (hfRec === r) hfRec = null; if (handsFree && hfPaused === 0) setTimeout(hfStart, 250); };
  r.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      setHandsFree(false);
      $("alexaSaid").textContent = "The microphone is blocked, so hands-free mode is off.";
    }
  };
  try { r.start(); hfRec = r; setLight("idle"); } catch { hfRec = null; }
}
function hfStop() { const r = hfRec; hfRec = null; if (r) { r.onend = null; try { r.abort(); } catch {} } }
function hfPause() { hfPaused++; hfStop(); }
function hfResume() { hfPaused = Math.max(0, hfPaused - 1); if (hfPaused === 0 && handsFree) setTimeout(hfStart, 300); }
// The view reports when a clip's sound starts and stops; the microphone stays closed in between.
let clipPlaying = false, clipGuard = null;
function onPlayback(state) {
  if (state === "playing" && !clipPlaying) {
    clipPlaying = true; hfPause();
    clipGuard = setTimeout(() => onPlayback("stopped"), 60000);  // never stay deaf if a "stopped" gets lost
  } else if (state !== "playing" && clipPlaying) {
    clipPlaying = false; clearTimeout(clipGuard); hfResume();
  }
}
function hfHeard(text) {
  const m = text.match(WAKE);
  const cmd = m ? m[1].trim() : Date.now() < hfAwaitUntil ? text : "";
  if (m && !cmd) { hfAwaitUntil = Date.now() + 8000; $("youSaid").textContent = "Alexa…"; setLight("listen"); return; }
  hfAwaitUntil = 0;
  if (!cmd) return;  // not addressed to Alexa: the cook talking to someone else, the TV, the video
  if (!localCommand(cmd)) ask(cmd);
}
// Things a device does by itself, without asking the agent.
function localCommand(cmd) {
  const c = cmd.toLowerCase().replace(/[.!?,]/g, "").trim();
  if (/^(stop|cancel|never ?mind|be quiet)$/.test(c)) {
    $("youSaid").textContent = cmd;
    speechSynthesis.cancel();
    return true;
  }
  if (/^(replay( (that|this|it|the clip))?|play (that|it) again|show me again)$/.test(c) && snap) {
    $("youSaid").textContent = cmd;
    const args = { session_id: snap.session_id, step_number: snap.step.step_number };
    mcp.call("show_moment", args).then((r) => onToolResult("show_moment", args, r));
    return true;
  }
  return false;
}
function setHandsFree(on) {
  handsFree = on && (!!SR || REC);
  $("hfBtn").classList.toggle("on", handsFree);
  $("hfBtn").querySelector("span").textContent = handsFree ? "Hands-free on" : "Hands-free";
  $("micBtn").classList.toggle("hf", handsFree);
  if (handsFree) { $("youSaid").textContent = "Say “Alexa, next”"; hfStart(); }
  else { hfStop(); setLight(""); }
  if (on && !SR && !REC) $("alexaSaid").textContent = "This browser has no speech recognition. Use Chrome for hands-free mode.";
}
$("hfBtn").onclick = () => setHandsFree(!handsFree);

// ---------------------------------------------------------------- talking to the agent

const pendingMoments = [];  // a moment is shown after Alexa has finished answering, so the clip never talks over her
async function ask(text) {
  text = text.replace(/^\s*(hey\s+)?alexa[,.!]?\s*/i, "").trim();
  if (!text) return;
  hfPause();
  try { await askAgent(text); } finally { hfResume(); }
}
async function askAgent(text) {
  $("youSaid").textContent = text;
  $("alexaSaid").textContent = "…";
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
    const result = { content: ev.text ? [{ type: "text", text: ev.text }] : [], structuredContent: ev.data || {}, isError: ev.status === "error" };
    if (ev.tool === "show_moment") pendingMoments.push([ev.tool, ev.input, result]);
    else onToolResult(ev.tool, ev.input, result);
  }
  $("alexaSaid").textContent = out.reply || "";
  await speak(out.reply);
  if (!REC) flushMoments();
}
function flushMoments() { for (const [t, a, r] of pendingMoments.splice(0)) onToolResult(t, a, r); }
$("typeForm").onsubmit = (e) => { e.preventDefault(); const t = $("typeInput").value; $("typeInput").value = ""; ask(t); };

// ---------------------------------------------------------------- loading a video

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
  await mountView();
  const args = { guide_id: g.guide_id };
  const s = await mcp.call("start_session", args);
  onToolResult("start_session", args, s);
  $("alexaSaid").textContent = `I watched the video and split it into ${g.steps.length} steps. Say next when you're ready.`;
  speak($("alexaSaid").textContent);
}
$("loadSample").onclick = () => importAndStart("sample:pasta");
// Watch another video: the launcher comes back over the add-on; pressing again returns to the cooking session.
$("newVideoBtn").onclick = () => {
  if (!$("launcher").hidden && view.frame) { $("launcher").hidden = true; view.frame.hidden = false; return; }
  $("launcher").hidden = false; $("importing").hidden = true;
  if (view.frame) view.frame.hidden = true;
  $("urlInput").focus();
};
$("urlForm").onsubmit = (e) => { e.preventDefault(); const u = $("urlInput").value.trim(); if (u) importAndStart(u); };

// ---------------------------------------------------------------- MCP trace drawer
// Server rows come from the server's /trace (both MCP clients: this host and the Strands agent);
// add-on rows are the MCP Apps messages between this host and the view.

let traceSince = 0, traceTimer = null;
const clientBySession = {};
const localRows = [];
function trace(row) {
  const r = { ts: Date.now() / 1000, ...row };
  localRows.push(r);
  if (!$("trace").hidden) addRow(r);
}
function addRow(r) {
  const box = $("traceRows");
  const el = document.createElement("div");
  el.className = "tr" + (r.ui ? " ui" : r.dir && r.dir.startsWith("server") ? " res" : "");
  const t = new Date(r.ts * 1000).toLocaleTimeString([], { hour12: false });
  el.innerHTML = `<div class="m"></div><div class="d"></div><div class="a"></div>`;
  el.querySelector(".m").textContent = r.method + (r.tool ? `  ${r.tool}` : "");
  el.querySelector(".d").textContent = `${t} · ${r.dir || ""}${r.who ? " · " + r.who : ""}`;
  el.querySelector(".a").textContent = r.detail || "";
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}
function serverRow(r) {
  if (r.client && r.session) clientBySession[r.session] = r.client;
  const detail = [];
  if (r.requested_protocol_version) detail.push(`requests protocol ${r.requested_protocol_version}`);
  if (r.negotiated_protocol_version) detail.push(`negotiated protocol ${r.negotiated_protocol_version} · server ${r.server}`);
  if (r.header_protocol_version) detail.push(`MCP-Protocol-Version: ${r.header_protocol_version}`);
  if (r.http_status) detail.push(`HTTP ${r.http_status}`);
  if (r.arguments) detail.push(JSON.stringify(r.arguments));
  return { ts: r.ts, method: r.method, tool: r.tool, dir: r.direction, who: r.client || clientBySession[r.session] || "", detail: detail.join("\n") };
}
async function pollTrace() {
  try {
    const rows = await (await fetch(`${mcpBase}/trace?since=${traceSince}`)).json();
    for (const r of rows) { traceSince = Math.max(traceSince, r.ts); addRow(serverRow(r)); }
  } catch {}
}
$("traceBtn").onclick = async () => {
  $("trace").hidden = false;
  $("traceRows").innerHTML = "";
  // history first: server traffic and add-on (MCP Apps) traffic, in time order
  let rows = [];
  try { rows = (await (await fetch(`${mcpBase}/trace?since=0`)).json()).map((r) => { traceSince = Math.max(traceSince, r.ts); return serverRow(r); }); } catch {}
  for (const r of [...rows, ...localRows].sort((a, b) => a.ts - b.ts).slice(-60)) addRow(r);
  traceTimer = traceTimer || setInterval(pollTrace, 1000);
};
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
    await mountView();
    onToolResult("get_session", {}, r);
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
  const words = text.split(" ");
  for (let i = 1; i <= words.length; i++) {
    $("youSaid").textContent = words.slice(0, i).join(" ");
    await new Promise((r) => setTimeout(r, ms / words.length));
  }
  $("micBtn").classList.remove("on"); setLight("");
  await ask(text);
  _logEvt("reply", $("alexaSaid").textContent);
};
window.__flushMoments = flushMoments;  // the recorder calls this once Alexa's recorded line has finished
