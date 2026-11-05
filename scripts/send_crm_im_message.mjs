import "./ws-shim.mjs";

// 通过 CRM 自己的发送接口把消息发出去（服务端下发 + 写入 CRM 消息表，看板可见）
//   1) POST /im/msg/check  内容校验（敏感词）
//   2) POST /im/msg/send   真正发送
// 用法: node send_crm_im_message.mjs --port=9223 --page=user-board --user-id=<学生userId> --term-id=<期次> --text=<内容> [--dry=1]
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const USER_ID = Number(arg("--user-id", "0"));
const TERM_ID = Number(arg("--term-id", "0"));
const TEXT = arg("--text", "");
const DRY = arg("--dry", "") === "1";

if (!USER_ID || !TERM_ID || !TEXT) {
  console.log("用法: node send_crm_im_message.mjs --user-id=<学生userId> --term-id=<期次> --text=<内容> [--dry=1]");
  process.exit(1);
}

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态，别因为页面切走就失效）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log(JSON.stringify({ ok: false, error: "没有可用的 CRM 页面（请打开任意 codemao 页面并保持登录）" }));
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}, timeoutMs = 90000) {
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

const expr = `(async () => {
  const base = 'https://cloud-gateway.codemao.cn/lbk-crm-realtime';
  const content = ${JSON.stringify(TEXT)};
  const userId = ${USER_ID};
  const termId = ${TERM_ID};
  const dry = ${DRY};
  const out = {};

  const post = async (path, body) => {
    const r = await fetch(base + path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) { json = null; }
    return { status: r.status, json: json, raw: text.slice(0, 400) };
  };

  // 1) 内容校验
  try {
    const chk = await post('/im/msg/check', { content: content });
    out.checkStatus = chk.status;
    out.check = chk.json || chk.raw;
    const result = chk.json && chk.json.checkResult;
    if (typeof result === 'number' && result !== 0) {
      out.ok = false;
      out.error = '内容未通过校验（checkResult=' + result + '）';
      out.hitKeywords = (chk.json && chk.json.hitKeyWordList) || [];
      out.proposeText = (chk.json && chk.json.proposeText) || '';
      return JSON.stringify(out);
    }
  } catch (e) {
    out.checkError = String((e && e.message) || e);
  }

  if (dry) { out.ok = true; out.dry = true; return JSON.stringify(out); }

  // 2) 真正发送
  const now = Date.now();
  const msgId = Math.random().toString(16).slice(2, 10) + '-' + now;
  const body = { termId: termId, sendTime: now, msgType: 1, msgId: msgId, userId: userId, platform: 1, content: content };
  try {
    const res = await post('/im/msg/send', body);
    out.sendStatus = res.status;
    out.send = res.json || res.raw;
    out.msgId = msgId;
    out.ok = res.status === 200;
    if (!out.ok) out.error = '发送接口返回 ' + res.status;
  } catch (e) {
    out.ok = false;
    out.error = '发送请求失败: ' + String((e && e.message) || e);
  }
  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
if (r.exceptionDetails) {
  console.log(JSON.stringify({ ok: false, error: "页面异常: " + JSON.stringify(r.exceptionDetails).slice(0, 400) }));
} else {
  console.log(r.result.value);
}
ws.close();
process.exit(0);
