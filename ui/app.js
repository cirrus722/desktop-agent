/* 前端只做两件事：订阅事件流、把事件画出来。
   所有状态都来自后端事件 —— agent 每一步做了什么，这里逐条渲染。 */

const $ = (id) => document.getElementById(id);
const chat = $("chat");

// ------------------------------------------------------------- WebSocket 事件流

let ws = null;
let running = false;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { $("conn-dot").className = "dot on"; $("status-text").textContent = "已连接"; };
  ws.onclose = () => {
    $("conn-dot").className = "dot off";
    $("status-text").textContent = "已断开，3 秒后重连";
    setTimeout(connect, 3000);
  };
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "ping") return;
    handleEvent(ev);
  };
}

// ------------------------------------------------------------- 事件 -> UI

function handleEvent(ev) {
  switch (ev.type) {
    case "run_started":
      running = true;
      $("btn-send").disabled = true;
      $("btn-stop").classList.remove("hidden");
      sysMsg(`▶ 开始运行（最多 ${ev.max_steps} 步）`);
      break;

    case "assistant_message":
      if (ev.text) bubble("assistant", ev.text);
      break;

    case "tool_call":
      currentToolCard = toolCard(ev);
      if (ev.source === "mcp") markMcpBadge();
      break;

    case "tool_result":
      if (currentToolCard) fillToolResult(currentToolCard, ev);
      if (ev.name === "read" && !ev.error) touchTree();
      if ((ev.name === "write" || ev.name === "edit") && !ev.error) { refreshTree(); if (ev.diff) showDiff(ev.diff); }
      if (ev.error) flashStatus("工具出错（红色卡片）—— 模型会看到错误并自救");
      currentToolCard = null;
      break;

    case "interrupted":
      running = false;
      sysMsg("■ 已中断（bash 进程已终止，循环已停止）");
      setDone();
      break;

    case "max_steps_reached":
      running = false;
      sysMsg(`⚠ 达到步数上限（${ev.max_steps}），任务未完成 —— 转人工`);
      setDone();
      break;

    case "llm_error":
      running = false;
      bubble("assistant", `❌ 模型调用失败：${ev.message}`);
      setDone();
      break;

    case "run_finished":
      running = false;
      sysMsg("✓ 本轮结束");
      setDone();
      break;
  }
}

let currentToolCard = null;

function setDone() {
  $("btn-send").disabled = false;
  $("btn-stop").classList.add("hidden");
}

// ------------------------------------------------------------- 渲染组件

function bubble(cls, text) {
  const d = document.createElement("div");
  d.className = `msg ${cls}`;
  d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
}

function sysMsg(text) {
  const d = document.createElement("div");
  d.className = "msg system";
  d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
}

function toolCard(ev) {
  const card = document.createElement("div");
  card.className = "toolcard";
  const args = JSON.stringify(ev.arguments, null, 1);
  const badge = ev.source === "mcp"
    ? `<span class="badge mcp">MCP</span>`
    : (ev.source === "skill" ? `<span class="badge skill">SKILL</span>` : "");
  card.innerHTML = `
    <div class="head">${badge}<span class="tname">${escapeHtml(ev.name)}</span>
      <span class="tmeta">step ${ev.step}</span></div>
    <div class="args">${escapeHtml(args)}</div>
    <div class="output">运行中…</div>`;
  chat.appendChild(card);
  chat.scrollTop = chat.scrollHeight;
  return card;
}

function fillToolResult(card, ev) {
  const out = card.querySelector(".output");
  const secs = ev.elapsed != null ? ` · ${ev.elapsed}s` : "";
  card.querySelector(".tmeta").textContent += secs;
  if (ev.error) card.classList.add("error");

  // 文本输出 + diff 块
  let html = "";
  const textParts = (ev.content || []).filter(b => b.type === "text").map(b => b.text);
  if (ev.diff) {
    html += `<div>${escapeHtml(textParts.join("\n").split("\n")[0])}</div>`; // 摘要行
    html += renderDiff(ev.diff);
  } else {
    html = escapeHtml(textParts.join("\n") || "(无文本输出)");
  }
  out.innerHTML = html;
  chat.scrollTop = chat.scrollHeight;
}

function renderDiff(diff) {
  return diff.split("\n").map(line => {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del" : "hdr";
    return `<span class="diffline ${cls}">${escapeHtml(line)}</span>`;
  }).join("");
}

function showDiff(diff) {
  const v = $("diff-view"), c = $("editor");
  v.classList.remove("hidden");
  c.classList.add("hidden");
  $("editor-title").textContent = "最近一次修改（diff）";
  v.innerHTML = renderDiff(diff);
}

function markMcpBadge() { $("mcp-badge").classList.remove("hidden"); }

function flashStatus(t) {
  $("status-text").textContent = t;
  setTimeout(() => { $("status-text").textContent = "已连接"; }, 2500);
}

// ------------------------------------------------------------- 文件树 / 编辑器

async function refreshTree() {
  const tree = await (await fetch("/api/tree")).json();
  renderTree($("tree"), tree, "");
}

function renderTree(container, items, prefix) {
  container.innerHTML = "";
  for (const it of items) {
    const d = document.createElement("div");
    d.className = `tree-item ${it.type}`;
    d.innerHTML = `<span class="ico">${it.type === "dir" ? "▾" : "·"}</span>${escapeHtml(it.name)}`;
    if (it.type === "file") d.onclick = () => openFile(prefix + it.name);
    container.appendChild(d);
    if (it.type === "dir" && it.children) {
      const sub = document.createElement("div");
      sub.className = "tree-children";
      container.appendChild(sub);
      renderTree(sub, it.children, prefix + it.name + "/");
    }
  }
}

let treeTouched = false;
function touchTree() { if (!treeTouched) { treeTouched = true; refreshTree(); } }

async function openFile(path) {
  const res = await (await fetch(`/api/file?path=${encodeURIComponent(path)}`)).json();
  if (res.error) { flashStatus(res.error); return; }
  const lines = res.content.split("\n");
  $("editor-code").innerHTML = lines.map((l, i) =>
    `<span><span class="ln">${i + 1}</span>${escapeHtml(l) || " "}</span>`).join("");
  $("editor-title").textContent = `${path} · ${lines.length} 行`;
  $("diff-view").classList.add("hidden");
  $("editor").classList.remove("hidden");
}

// ------------------------------------------------------------- 发送 / 中断

function send() {
  const input = $("input");
  const text = input.value.trim();
  if (!text || running) return;
  bubble("user", text);
  input.value = "";
  ws.send(JSON.stringify({ type: "message", text }));
}

$("btn-send").onclick = send;
$("input").addEventListener("keydown", e => { if (e.key === "Enter") send(); });
$("btn-stop").onclick = () => ws.send(JSON.stringify({ type: "stop" }));

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ------------------------------------------------------------- 启动

connect();
refreshTree();
(async () => {
  const hist = await (await fetch("/api/history")).json();
  for (const m of hist.slice(-20)) bubble(m.role === "user" ? "user" : "assistant", m.text);
  if (hist.length) sysMsg("（已恢复上次会话）");
})();
