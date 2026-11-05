import "./ws-shim.mjs";
import fs from "node:fs";

// 通用：把文件里的表达式丢进指定页面求值并打印结果
// 用法: node cdp_eval_expr.mjs --port=9223 --page=user-board --file=tmp/expr.js [--await=1] [--max=4000]
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const FILE = arg("--file", "");
const AWAIT = arg("--await", "") === "1";
const MAX = Number(arg("--max", "4000"));

if (!FILE) {
  console.log("用法: node cdp_eval_expr.mjs --page=<match> --file=<expr.js> [--await=1] [--max=N]");
  process.exit(1);
}
const expr = fs.readFileSync(FILE, "utf8");

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态，页面切走也不会失效）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log("未找到页面:", PAGE_MATCH);
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}, timeoutMs = 120000) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error("timeout " + method));
      }
    }, timeoutMs);
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

const r = await send("Runtime.evaluate", {
  expression: expr,
  awaitPromise: AWAIT,
  returnByValue: true,
});
if (r.exceptionDetails) {
  console.log("异常:", JSON.stringify(r.exceptionDetails).slice(0, 1500));
} else {
  const v = r.result && r.result.value;
  console.log(typeof v === "string" ? v.slice(0, MAX) : JSON.stringify(v).slice(0, MAX));
}
ws.close();
process.exit(0);
