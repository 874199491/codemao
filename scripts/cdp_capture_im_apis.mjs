import "./ws-shim.mjs";

const PORT = 9223;
const MATCH = process.argv[2] || "user-board";
const WAIT_MS = Number(process.argv[3] || 20000);

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find((t) => t.type === "page" && t.url.includes(MATCH));
if (!page) {
  console.log("未找到匹配页面:", MATCH);
  process.exit(1);
}
console.log("target:", page.url.slice(0, 120));

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => pending.has(id) && (pending.delete(id), reject(new Error(`timeout ${method}`))), 20000);
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
await send("Runtime.enable");

const inject = `(() => {
  if (window.__imApiLog) return 'already';
  window.__imApiLog = [];
  const log = (o) => { try { window.__imApiLog.push(o); } catch (e) {} };
  const of = window.fetch;
  window.fetch = async function (...args) {
    const a0 = args[0];
    const url = typeof a0 === 'string' ? a0 : (a0 && a0.url) || '';
    const method = (args[1] && args[1].method) || (a0 && a0.method) || 'GET';
    const body = args[1] && args[1].body ? String(args[1].body).slice(0, 400) : '';
    let status = 0;
    try { const res = await of.apply(this, args); status = res.status; return res; }
    finally { log({ url, method, body, status }); }
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u, ...rest) { this.__m = m; this.__u = u; return oo.call(this, m, u, ...rest); };
  XMLHttpRequest.prototype.send = function (...args) {
    const body = args[0] ? String(args[0]).slice(0, 400) : '';
    this.addEventListener('loadend', () => log({ url: this.__u, method: this.__m, body, status: this.status }));
    return os.apply(this, args);
  };
  const OWS = window.WebSocket;
  if (OWS) {
    window.WebSocket = function (url, protocols) { log({ url: 'WS ' + url, method: 'WS', body: '', status: 0 }); return new OWS(url, protocols); };
  }
  return 'injected';
})()`;

const r1 = await send("Runtime.evaluate", { expression: inject, returnByValue: true });
console.log("inject:", r1.result && r1.result.value);
console.log(`waiting ${WAIT_MS}ms to collect requests...`);
await new Promise((r) => setTimeout(r, WAIT_MS));

const r2 = await send("Runtime.evaluate", { expression: "JSON.stringify(window.__imApiLog || [])", returnByValue: true });
const log = JSON.parse(r2.result.value || "[]");
console.log("captured:", log.length);
for (const item of log) {
  console.log(`[${item.status}] ${item.method} ${String(item.url).slice(0, 150)}${item.body ? "  body=" + item.body.slice(0, 120) : ""}`);
}
ws.close();
process.exit(0);
