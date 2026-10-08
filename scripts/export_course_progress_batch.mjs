import "./ws-shim.mjs";
import fs from "node:fs";
import path from "node:path";

function arg(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const port = Number(arg("--port", "9223"));
const configPath = path.resolve(arg("--config", "data/teacher-workbench-config.json"));
const outDir = path.resolve(arg("--out-dir", "data/course-progress-exports"));
const requestedIds = process.argv
  .map((value, index) => (value === "--class-id" ? Number(process.argv[index + 1]) : null))
  .filter((value) => Number.isInteger(value) && value > 0);

function safeName(value) {
  return String(value || "班级").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim() || "班级";
}

function loadTargets() {
  if (requestedIds.length) return requestedIds.map((classId) => ({ classId, label: `班级-${classId}` }));
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const classes = config?.profile?.classes || config?.profile?.crm?.classes || config?.classes || [];
  return classes
    .map((item) => ({ classId: Number(item.class_id ?? item.classId), label: item.label || item.class_name || `班级-${item.class_id}` }))
    .filter((item) => Number.isInteger(item.classId) && item.classId > 0);
}

const targets = loadTargets();
if (!targets.length) throw new Error("没有可导出的班级。请在配置中填写 classes，或重复传入 --class-id。");

const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = list.find((item) => item.type === "page" && /codecamp-crm\.codemao\.cn/.test(item.url))
  || list.find((item) => item.type === "page" && /codemao\.cn/.test(item.url));
if (!page?.webSocketDebuggerUrl) throw new Error(`Chrome 调试端口 ${port} 没有已登录的 CRM 页面`);

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let sequence = 0;
function send(method, params = {}) {
  const id = ++sequence;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`等待 ${method} 超时`));
      }
    }, 120000);
  });
}
ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const current = pending.get(message.id);
  pending.delete(message.id);
  message.error ? current.reject(new Error(JSON.stringify(message.error))) : current.resolve(message.result);
});
await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));

async function evaluate(expression) {
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails).slice(0, 2000));
  return result.result.value;
}

fs.mkdirSync(outDir, { recursive: true });
const results = [];
for (const target of targets) {
  const payload = await evaluate(`(async()=>{
    const response = await fetch("https://lbk-crm-teacher-web-api.codemao.cn/classes/exportCourseProgress", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json;charset=UTF-8", "authorization_type": "3" },
      body: JSON.stringify({ classId: ${target.classId} })
    });
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    return { ok: response.ok, status: response.status, base64: btoa(binary), contentType: response.headers.get("content-type") || "" };
  })()`);
  if (!payload?.ok) throw new Error(`班级 ${target.classId} 导出失败：HTTP ${payload?.status}`);
  const filePath = path.join(outDir, `${safeName(target.label)}-${target.classId}-courseProcess.xlsx`);
  fs.writeFileSync(filePath, Buffer.from(payload.base64, "base64"));
  results.push({ ...target, file: filePath, bytes: fs.statSync(filePath).size });
}
ws.close();
console.log(JSON.stringify({ count: results.length, outDir, files: results }, null, 2));
