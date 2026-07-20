// Cliente WebSocket + UI do Jarvis.
const $ = (sel) => document.querySelector(sel);
const logEl = $("#log");
const workerEl = $("#worker-out");
const statusEl = $("#status");
const textEl = $("#text");

let ws;

function setStatus(text) {
  statusEl.textContent = text;
}

function addMsg(container, role, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role;
  el.appendChild(who);
  const body = document.createElement("div");
  body.textContent = text;
  el.appendChild(body);
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

function addWorkerBlock(kind, text) {
  if (kind === "code") {
    const pre = document.createElement("pre");
    pre.textContent = text;
    workerEl.appendChild(pre);
  } else if (kind === "tool") {
    const el = document.createElement("div");
    el.className = "tool";
    el.textContent = `🔧 ${text}`;
    workerEl.appendChild(el);
  } else {
    addMsg(workerEl, "worker", text);
  }
  workerEl.scrollTop = workerEl.scrollHeight;
}

function addApproval(text) {
  addMsg(logEl, "jarvis", text);
  const el = document.createElement("div");
  el.className = "approval";
  const yes = document.createElement("button");
  yes.textContent = "Autorizar";
  const no = document.createElement("button");
  no.textContent = "Negar";
  yes.onclick = () => {
    send({ type: "control", action: "approve" });
    el.remove();
  };
  no.onclick = () => {
    send({ type: "control", action: "deny" });
    el.remove();
  };
  el.append(yes, no);
  logEl.appendChild(el);
  logEl.scrollTop = logEl.scrollHeight;
}

function playAudio(b64, format = "wav") {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const blob = new Blob([bytes], { type: `audio/${format}` });
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  audio.play().finally(() => URL.revokeObjectURL(url));
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setStatus("conectado");
  ws.onclose = () => {
    setStatus("desconectado — reconectando…");
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "status":
        setStatus(msg.text || msg.state || "");
        break;
      case "transcript":
        addMsg(logEl, msg.role, msg.text);
        break;
      case "worker":
        addWorkerBlock(msg.kind || "text", msg.text || "");
        break;
      case "handoff":
        $("#worker-model").textContent = `${msg.model} · ${msg.effort}`;
        addMsg(logEl, "jarvis", `Prompt gerado (${msg.model}/${msg.effort}).`);
        addWorkerBlock("code", msg.prompt);
        break;
      case "tts":
        if (msg.data) playAudio(msg.data, msg.format || "wav");
        break;
      case "question":
        addMsg(logEl, "jarvis", msg.text || "(pergunta)");
        setStatus("aguardando sua resposta…");
        textEl.focus();
        break;
      case "approval":
        addApproval(msg.text || "Autorizar esta ação?");
        setStatus("aguardando autorização…");
        break;
    }
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function sendText() {
  const t = textEl.value.trim();
  if (!t) return;
  send({ type: "text", text: t });
  textEl.value = "";
}

$("#send").addEventListener("click", sendText);
textEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendText();
});
$("#end").addEventListener("click", () =>
  send({ type: "control", action: "end_conversation" })
);

// --- Push-to-talk (MediaRecorder). STT no backend é ligado na fase de voz. ---
let mediaRecorder = null;
let chunks = [];

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    chunks = [];
    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType });
      const buf = await blob.arrayBuffer();
      const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
      send({ type: "audio", format: "webm", data: b64 });
    };
    mediaRecorder.start();
    $("#mic").classList.add("recording");
  } catch (err) {
    setStatus("sem acesso ao microfone");
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  $("#mic").classList.remove("recording");
}

const mic = $("#mic");
mic.addEventListener("mousedown", startRecording);
mic.addEventListener("mouseup", stopRecording);
mic.addEventListener("mouseleave", stopRecording);
mic.addEventListener("touchstart", (e) => {
  e.preventDefault();
  startRecording();
});
mic.addEventListener("touchend", (e) => {
  e.preventDefault();
  stopRecording();
});

connect();
