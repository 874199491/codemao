import "./ws-shim.mjs";

// 轻量轮询：先只调 1 次 studentList 拿到所有学员的「最后一条消息」，
// 只对最后一条来自学生的学员再拉消息历史。
// 一次轮询通常 1~3 个请求，秒级返回，适合做自动回复的触发检测。
// 用法: node poll_im_new_messages.mjs --port=9223 --page=user-board [--history-limit=6] [--out=tmp/poll.json]
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const HL = Number(arg("--history-limit", "6"));

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态，页面切走也不会失效）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log(JSON.stringify({ ok: false, error: "未找到 CRM 页面: " + PAGE_MATCH }));
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

const expr = `(async () => {
  const CRM = 'https://cloud-gateway.codemao.cn/crm-rocket';
  const RT = 'https://cloud-gateway.codemao.cn/lbk-crm-realtime';
  const get = async (u) => {
    try {
      const r = await fetch(u, { credentials: 'include' });
      const t = await r.text();
      try { return JSON.parse(t); } catch (e) { return { _status: r.status, _raw: t.slice(0, 200) }; }
    } catch (e) { return { _status: 0, _err: String((e && e.message) || e) }; }
  };

  const out = { ok: true, checkedAt: Date.now(), totalStudents: 0, allUnreadCount: 0, candidates: [] };

  const list = await get(CRM + '/im/teacher/studentList');
  if (list && list._status) {
    return JSON.stringify({ ok: false, error: 'studentList 失败', detail: JSON.stringify(list).slice(0, 300) });
  }
  const students = (list && list.students) || [];
  out.totalStudents = students.length;
  out.allUnreadCount = (list && list.allUnreadCount) || 0;

  // 只挑「最后一条消息来自学生」的人（1=学生, 4=学生求助）
  const need = [];
  for (const s of students) {
    const lm = s.lastMsg;
    if (!lm) continue;
    if (lm.msgSender !== 1 && lm.msgSender !== 4) continue;
    need.push(s);
  }

  const norm = (m) => {
    const c = m.content;
    const isObj = c && typeof c === 'object';
    return {
      msgType: m.msgType,
      sendTime: m.sendTime,
      from: m.from && m.from.type,
      to: m.to && m.to.type,
      text: typeof c === 'string' ? c : '',
      card: isObj ? { cardType: c.cardType || '', title: c.title || '', stem: (c.stem && c.stem.text) || '' } : null,
      media: isObj && c.voiceUrl ? { voiceUrl: c.voiceUrl } : (isObj && c.rtcId ? { rtcId: c.rtcId } : null),
      help: isObj && c.requestId ? { requestId: c.requestId } : null,
      transerTxt: m.transerTxt || ''
    };
  };

  let cursor = 0;
  async function worker() {
    while (cursor < need.length) {
      const s = need[cursor++];
      const h = await get(RT + '/im/msg/history?userId=' + s.userId + '&termId=' + s.termId + '&limit=' + ${HL});
      const msgs = Array.isArray(h) ? h : (h && h.data) || [];
      out.candidates.push({
        userId: s.userId,
        name: s.childName || s.name || '',
        termId: s.termId,
        classId: s.classId,
        className: s.className || '',
        termName: s.termName || '',
        unreadCount: s.unreadCount || 0,
        lastMsg: s.lastMsg ? {
          msgType: s.lastMsg.msgType,
          sendTime: s.lastMsg.sendTime,
          msgSender: s.lastMsg.msgSender
        } : null,
        messages: msgs.map(norm)
      });
    }
  }
  await Promise.all([worker(), worker(), worker(), worker()]);
  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
if (r.exceptionDetails) {
  console.log(JSON.stringify({ ok: false, error: "页面异常", detail: JSON.stringify(r.exceptionDetails).slice(0, 400) }));
} else {
  const value = r.result.value || "{}";
  const OUT = arg("--out", "");
  if (OUT) {
    const fs = await import("node:fs");
    const path = await import("node:path");
    fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
    fs.writeFileSync(path.resolve(OUT), value, "utf8");
  }
  console.log(value);
}
ws.close();
process.exit(0);
