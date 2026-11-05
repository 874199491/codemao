import "./ws-shim.mjs";
import fs from "node:fs";

const PORT = 9223;
function flag(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}
const positional = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const pageMatch = flag("--page", positional[0]);
const url = flag("--url", positional[1]);
const method = String(flag("--method", positional[2] || "GET")).toUpperCase();
// 纯数字的第 4 个位置参数视为 maxLen（PowerShell 会丢掉空参数槽，导致无法跳过 body）
let bodyArg = flag("--body", "");
if (!bodyArg) {
  if (positional[3] && /^\d+$/.test(positional[3])) {
    bodyArg = "";
  } else {
    bodyArg = positional[3] || "";
  }
}
let maxLen = Number(flag("--max-len", "0"));
if (!maxLen) {
  if (positional[3] && /^\d+$/.test(positional[3])) maxLen = Number(positional[3]);
  else maxLen = Number(positional[4] || 0) || 2500;
}

// 以 @ 开头表示从文件读取请求体，避免命令行转义问题。
if (bodyArg.startsWith("@")) {
  bodyArg = fs.readFileSync(bodyArg.slice(1), "utf8").trim();
}

if (!pageMatch || !url) {
  console.log("用法: node cdp_page_fetch.mjs <pageMatch> <url> [METHOD] [bodyJson] [maxLen]");
  process.exit(1);
}

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(pageMatch)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log("没有可用的 CRM 页面（请打开任意 codemao 页面并保持登录）");
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(m, p = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method: m, params: p }));
  return new Promise((res, rej) => {
    pending.set(id, { res, rej });
    setTimeout(() => pending.has(id) && (pending.delete(id), rej(new Error("timeout " + m))), 30000);
  });
}
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (!msg.id || !pending.has(msg.id)) return;
  const { res, rej } = pending.get(msg.id);
  pending.delete(msg.id);
  msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
});
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
await send("Runtime.enable");

const expr = `(async () => {
  try {
    const res = await fetch(${JSON.stringify(url)}, {
      method: ${JSON.stringify(method)},
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      ${bodyArg ? `body: ${JSON.stringify(bodyArg)},` : ""}
    });
    const text = await res.text();
    return JSON.stringify({ status: res.status, body: text });
  } catch (e) { return JSON.stringify({ status: 0, body: 'ERR: ' + e.message }); }
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
let out;
try {
  out = JSON.parse(r.result.value);
} catch {
  out = { status: 0, body: String(r.result && r.result.value) };
}
console.log("HTTP", out.status);
console.log(String(out.body).slice(0, maxLen));
ws.close();
process.exit(0);
