// Cliente WebSocket + UI do Jarvis.
const $ = (sel) => document.querySelector(sel);
const logEl = $("#log");
const workerEl = $("#worker-out");
const statusEl = $("#status");
const textEl = $("#text");
const repoEl = $("#repo");

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
      case "repo":
        repoEl.value = msg.path || "";
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

// --- Anexos (imagens, PDFs, texto/código…) ---
let pendingAttachments = [];
const attachmentsEl = $("#attachments");
const fileEl = $("#file");

function fileToAttachment(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = String(reader.result).split(",")[1] || "";
      resolve({
        kind: file.type.startsWith("image/") ? "image" : "file",
        media_type: file.type || "application/octet-stream",
        data: b64,
        name: file.name || "arquivo",
      });
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function renderAttachments() {
  attachmentsEl.innerHTML = "";
  pendingAttachments.forEach((att, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    if (att.kind === "image") {
      const img = document.createElement("img");
      img.src = `data:${att.media_type};base64,${att.data}`;
      chip.appendChild(img);
    }
    const name = document.createElement("span");
    name.textContent = att.name;
    chip.appendChild(name);
    const rm = document.createElement("button");
    rm.textContent = "✕";
    rm.onclick = () => {
      pendingAttachments.splice(i, 1);
      renderAttachments();
    };
    chip.appendChild(rm);
    attachmentsEl.appendChild(chip);
  });
}

async function addFiles(fileList) {
  for (const file of fileList) {
    try {
      pendingAttachments.push(await fileToAttachment(file));
    } catch {
      setStatus(`falha ao ler ${file.name}`);
    }
  }
  renderAttachments();
}

function sendText() {
  const t = textEl.value.trim();
  if (!t && pendingAttachments.length === 0) return;
  send({ type: "text", text: t, attachments: pendingAttachments });
  textEl.value = "";
  pendingAttachments = [];
  renderAttachments();
}

$("#send").addEventListener("click", sendText);
textEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendText();
});

$("#attach").addEventListener("click", () => fileEl.click());
fileEl.addEventListener("change", () => {
  if (fileEl.files.length) addFiles(fileEl.files);
  fileEl.value = "";
});
textEl.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) {
    e.preventDefault();
    addFiles(files);
  }
});
$("#end").addEventListener("click", () =>
  send({ type: "control", action: "end_conversation" })
);

function setRepo() {
  send({ type: "control", action: "set_repo", path: repoEl.value.trim() });
}
$("#set-repo").addEventListener("click", setRepo);
repoEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") setRepo();
});

// Prefill do repositório com o padrão do servidor (se houver).
fetch("/api/config")
  .then((r) => r.json())
  .then((c) => {
    if (c.target_repo && !repoEl.value) repoEl.value = c.target_repo;
  })
  .catch(() => {});

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
