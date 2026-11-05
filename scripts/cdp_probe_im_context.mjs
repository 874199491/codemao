import "./ws-shim.mjs";

// 在 user-board 页面内探测「期次(term)」上下文，找出 userId -> termId 的可用来源
const PORT = Number((process.argv.find((a) => a.startsWith("--port=")) || "--port=9223").split("=")[1]);
const PAGE_MATCH = (process.argv.find((a) => a.startsWith("--page=")) || "--page=user-board").split("=")[1];

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

const expr = `(() => {
  const out = { url: location.href, selects: [], storage: {}, domTerm: [], vue: [] };

  document.querySelectorAll('select').forEach((s) => {
    out.selects.push({
      name: s.name || '', id: s.id || '', cls: String(s.className || '').slice(0, 60),
      options: Array.from(s.options).slice(0, 60).map((o) => ({ v: o.value, t: o.text }))
    });
  });

  for (const [label, st] of [['local', localStorage], ['session', sessionStorage]]) {
    for (let i = 0; i < st.length; i++) {
      const k = st.key(i);
      if (/term|class|course|period|stage/i.test(k)) {
        out.storage[label + ':' + k] = String(st.getItem(k)).slice(0, 400);
      }
    }
  }

  // DOM 上带 term / 期次 字样的元素
  const all = document.querySelectorAll('*');
  for (const el of all) {
    const ds = el.dataset || {};
    for (const k of Object.keys(ds)) {
      if (/term|classid|course/i.test(k)) {
        out.domTerm.push({ tag: el.tagName, cls: String(el.className || '').slice(0, 50), attr: k, val: String(ds[k]).slice(0, 60) });
      }
    }
    if (out.domTerm.length > 40) break;
  }

  // 页面可见文本里含「期」的短文本
  const txt = [];
  for (const el of all) {
    if (el.children.length === 0) {
      const t = (el.textContent || '').trim();
      if (t && t.length < 40 && /期|班/.test(t)) txt.push(t);
    }
    if (txt.length > 60) break;
  }
  out.texts = Array.from(new Set(txt));

  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
console.log(r.result.value);
ws.close();
process.exit(0);
