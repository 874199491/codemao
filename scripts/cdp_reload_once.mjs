import "./ws-shim.mjs";

const PORT = 9223;
const MATCH = process.argv[2] || "user-board";

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find((t) => t.type === "page" && t.url.includes(MATCH));
if (!page) {
  console.log("未找到匹配页面:", MATCH);
  process.exit(1);
}
console.log("reloading:", page.url.slice(0, 120));

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => pending.has(id) && (pending.delete(id), reject(new Error(`timeout ${method}`))), 15000);
  });
}
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (!m.id || !pending.has(m.id)) return;
  const { resolve, reject } = pending.get(m.id);
  pending.delete(m.id);
  m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
});
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
await send("Page.enable");
await send("Page.reload", { ignoreCache: true });
console.log("reload sent");
setTimeout(() => { ws.close(); process.exit(0); }, 1500);
