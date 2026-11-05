import "./ws-shim.mjs";
import fs from "node:fs";
import path from "node:path";

// 用 CDP Network 域抓取页面发出的完整请求 URL（可选先 reload）
// 用法: node cdp_capture_network.mjs --port=9223 --page=user-board --seconds=45 --reload=1 --filter=im|class --out=tmp/net.json
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const SECONDS = Number(arg("--seconds", "45"));
const RELOAD = arg("--reload", "") === "1";
const FILTER = arg("--filter", "");
const OUT = arg("--out", "");

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态，页面切走也不会失效）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log("未找到页面:", PAGE_MATCH);
  process.exit(1);
}
console.log("目标页面:", page.url);

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

const seen = new Map(); // url(no query tail) -> {url, method, count, postData}
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id);
    pending.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    return;
  }
  if (m.method === "Network.requestWillBeSent") {
    const req = m.params.request;
    const url = req.url;
    if (!/^https?:/i.test(url)) return;
    if (FILTER && !new RegExp(FILTER, "i").test(url)) return;
    const key = req.method + " " + url;
    const hit = seen.get(key);
    if (hit) {
      hit.count++;
    } else {
      seen.set(key, { method: req.method, url, count: 1, postData: req.postData || null });
    }
  }
});

await new Promise((r) => ws.addEventListener("open", r, { once: true }));
await send("Network.enable");
if (RELOAD) {
  await send("Page.enable");
  await send("Page.reload", { ignoreCache: false });
  console.log("已触发页面重载，采集", SECONDS, "秒 ...");
} else {
  console.log("采集", SECONDS, "秒（未重载）...");
}
await new Promise((r) => setTimeout(r, SECONDS * 1000));

const list = Array.from(seen.values()).sort((a, b) => b.count - a.count);
console.log("捕获请求数:", list.length);
for (const item of list) {
  console.log(`${item.count}x ${item.method} ${item.url}`);
  if (item.postData) console.log("    body:", String(item.postData).slice(0, 300));
}
if (OUT) {
  fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify({ capturedAt: new Date().toISOString(), page: page.url, requests: list }, null, 2), "utf8");
  console.log("已写入", OUT);
}
ws.close();
process.exit(0);
