import "./ws-shim.mjs";

// 探测页面内的环信/WebIM 发送能力，寻找可编程的「发消息」入口
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
function send(method, params = {}, timeoutMs = 60000) {
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
  const out = { globals: [], candidates: [], vue: [], iframes: 0, sends: [] };
  for (const k of Object.keys(window)) {
    if (/webim|easemob|socket|chat/i.test(k)) out.globals.push(k + ' :: ' + typeof window[k]);
  }
  try {
    for (const k of Object.keys(window)) {
      let o;
      try { o = window[k]; } catch (e) { continue; }
      if (!o || typeof o !== 'object') continue;
      try {
        if (typeof o.send === 'function') out.candidates.push('window.' + k + '.send');
        if (o.connection && typeof o.connection.send === 'function') out.candidates.push('window.' + k + '.connection.send');
        if (o.conn && typeof o.conn.send === 'function') out.candidates.push('window.' + k + '.conn.send');
        if (typeof o.sendTextMessage === 'function') out.candidates.push('window.' + k + '.sendTextMessage');
      } catch (e) {}
    }
  } catch (e) { out.candidates.push('scan-err ' + e.message); }

  out.iframes = document.querySelectorAll('iframe').length;
  const app = document.querySelector('#app') || document.body.firstElementChild;
  if (app) {
    const keys = Object.keys(app).filter((k) => k.startsWith('__vue'));
    out.vue.push('el keys: ' + keys.join(',') || 'none');
    let v = app.__vue__ || null;
    if (!v && app.__vue_app__ && app.__vue_app__._instance) v = app.__vue_app__._instance.proxy;
    out.vue.push('instance: ' + (v ? 'yes' : 'no'));
    if (v) {
      out.vue.push('name: ' + ((v.$options && v.$options.name) || '?'));
      out.vue.push('$data: ' + Object.keys(v.$data || {}).join(',').slice(0, 400));
      out.vue.push('$children: ' + (v.$children || []).length);
    }
  }
  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
console.log(r.result.value);
ws.close();
process.exit(0);
