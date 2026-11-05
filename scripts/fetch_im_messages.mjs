import "./ws-shim.mjs";
import fs from "node:fs";
import path from "node:path";

// 同时支持 "--name=value" 与 "--name value" 两种写法
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  if (hit) return hit.slice(eq.length) || fallback;
  const idx = process.argv.indexOf(name);
  if (idx >= 0 && process.argv[idx + 1] && !process.argv[idx + 1].startsWith("--")) {
    return process.argv[idx + 1];
  }
  return fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const TERM_ID = arg("--term-id", "");
const IDS_FILE = arg("--ids-file", "");
const LIMIT = arg("--limit", "30");
const OUT = arg("--out", "data/im-messages.json");
const CONCURRENCY = Number(arg("--concurrency", "5"));

let userIds = [];
if (IDS_FILE) {
  userIds = fs
    .readFileSync(IDS_FILE, "utf8")
    .split(/[\r\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
if (!userIds.length) {
  console.log("缺少 --ids-file（每行一个 userId）");
  process.exit(1);
}
console.log("学员数:", userIds.length, "termId:", TERM_ID, "limit:", LIMIT);

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态，页面切走也不会失效）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log("未找到匹配页面:", PAGE_MATCH);
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}, timeoutMs = 600000) {
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

// 在页面内并发拉取每个学员的消息历史（复用页面登录态）
const expr = `(async () => {
  const ids = ${JSON.stringify(userIds)};
  const termId = ${JSON.stringify(String(TERM_ID))};
  const limit = ${JSON.stringify(String(LIMIT))};
  const pool = ${JSON.stringify(CONCURRENCY)};
  const out = {};
  let cursor = 0;
  async function worker() {
    while (cursor < ids.length) {
      const uid = ids[cursor++];
      try {
        const res = await fetch('https://cloud-gateway.codemao.cn/lbk-crm-realtime/im/msg/history?userId=' + uid + '&termId=' + termId + '&limit=' + limit, { credentials: 'include' });
        const text = await res.text();
        let json = null;
        try { json = JSON.parse(text); } catch (e) { json = null; }
        out[uid] = { status: res.status, messages: Array.isArray(json) ? json : (json && json.data) || [] };
      } catch (e) {
        out[uid] = { status: 0, error: String(e && e.message || e), messages: [] };
      }
    }
  }
  await Promise.all(Array.from({ length: pool }, () => worker()));
  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
const data = JSON.parse(r.result.value || "{}");
const total = Object.values(data).reduce((acc, item) => acc + (item.messages ? item.messages.length : 0), 0);
fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
fs.writeFileSync(path.resolve(OUT), JSON.stringify({ fetchedAt: new Date().toISOString(), termId: TERM_ID, students: data }, null, 2), "utf8");
console.log("学员:", Object.keys(data).length, "消息:", total, "-> ", OUT);
ws.close();
process.exit(0);
