import "./ws-shim.mjs";
import fs from "node:fs";
import path from "node:path";

// 在已登录的 CRM 页面里扫描 IM 答疑全景：
//   1) im/teacher/studentList   -> 全部学员 + 未读数 + 最后一条消息 + termId
//   2) im/msg/history           -> 待关注学员的消息历史
//   3) normalClass/.../multiple -> 学员最新课程的作答与 OJ 代码
// 用法: node fetch_im_overview.mjs --port=9223 --page=user-board --out=data/im-overview.json
//        [--history-limit=20] [--all=1] [--max-students=40] [--with-questions=1]
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const OUT = arg("--out", "data/im-overview.json");
const HL = Number(arg("--history-limit", "20"));
const ALL = arg("--all", "") === "1";
const MAXS = Number(arg("--max-students", "40"));
const WITH_Q = arg("--with-questions", "1") === "1";

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
function send(method, params = {}, timeoutMs = 300000) {
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
  const j = async (u, opt) => {
    try {
      const r = await fetch(u, Object.assign({ credentials: 'include' }, opt || {}));
      const t = await r.text();
      try { return JSON.parse(t); } catch (e) { return { _status: r.status, _raw: t.slice(0, 300) }; }
    } catch (e) { return { _status: 0, _err: String((e && e.message) || e) }; }
  };
  const post = (u, b) => j(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
  const get = (u) => j(u);

  const out = { scannedAt: Date.now(), allUnreadCount: 0, students: [], targetIds: [], messages: {}, questions: {}, errors: [] };

  // 1) 学员全景
  const list = await get(CRM + '/im/teacher/studentList');
  if (list && list._status) out.errors.push('studentList: ' + JSON.stringify(list).slice(0, 200));
  const arr = (list && list.students) || [];
  out.allUnreadCount = (list && list.allUnreadCount) || 0;
  out.students = arr.map((s) => ({
    userId: s.userId,
    name: s.childName || s.name || '',
    fullName: s.name || '',
    termId: s.termId,
    termName: s.termName || '',
    classId: s.classId,
    className: s.className || '',
    // 发消息用的环信 ID（形如 388277333_1726824523163）
    imId: ((s.IMIdList || [])[0] || {}).IMId || '',
    unreadCount: s.unreadCount || 0,
    inTerm: !!s.inTerm,
    later: !!s.later,
    lastMsg: s.lastMsg ? {
      msgType: s.lastMsg.msgType,
      content: s.lastMsg.content,
      sendTime: s.lastMsg.sendTime,
      msgSender: s.lastMsg.msgSender,
      isRead: s.lastMsg.isRead,
      isRetract: s.lastMsg.isRetract
    } : null
  }));

  // 2) 选出待关注学员：有未读，或最后一条来自学生(1=学生,4=学生求助)
  let targets = ${ALL}
    ? out.students.slice()
    : out.students.filter((s) => s.unreadCount > 0 || (s.lastMsg && (s.lastMsg.msgSender === 1 || s.lastMsg.msgSender === 4)));
  if (targets.length > ${MAXS}) targets = targets.slice(0, ${MAXS});
  out.targetIds = targets.map((s) => s.userId);

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
      media: isObj && c.voiceUrl ? { voiceUrl: c.voiceUrl, duration: c.voiceDuration } : (isObj && c.rtcId ? { rtcId: c.rtcId, status: c.status } : null),
      help: isObj && c.requestId ? { requestId: c.requestId, status: c.status } : null,
      transerTxt: m.transerTxt || '',
      aiMsg: m.aiMsg || 0
    };
  };

  // 3) 并发拉消息历史
  let cursor = 0;
  async function historyWorker() {
    while (cursor < targets.length) {
      const s = targets[cursor++];
      const h = await get(RT + '/im/msg/history?userId=' + s.userId + '&termId=' + s.termId + '&limit=' + ${HL});
      const msgs = Array.isArray(h) ? h : (h && h.data) || [];
      out.messages[s.userId] = msgs.map(norm);
    }
  }
  await Promise.all([historyWorker(), historyWorker(), historyWorker(), historyWorker()]);

  // 4) 对有求助/提问的学员拉最新课程作答与代码
  if (${WITH_Q}) {
    const HELPY = /代码|程序|运行|报错|怎么做|不会|帮我|帮忙|看看|卡住|求助|老师|问题|不懂|不明白|教我|讲讲|为什么|对不对/;
    const needQ = targets.filter((s) => {
      const msgs = out.messages[s.userId] || [];
      // 看最近几条学生消息，别只盯最后一句（学生常以「好的」收尾）
      const recent = msgs.filter((m) => m.from === 1 || m.from === 4).slice(-6);
      if (!recent.length) return false;
      return recent.some(
        (m) => m.from === 4 || m.msgType === 21 || HELPY.test((m.text || '') + (m.transerTxt || ''))
      );
    });
    let c2 = 0;
    async function qWorker() {
      while (c2 < needQ.length) {
        const s = needQ[c2++];
        const uc = await get(CRM + '/inClassBoard/unlockCourse/' + s.termId + '?classId=' + s.classId);
        const courses = (uc && uc.data) || [];
        const unlocked = courses.filter((c) => c.unlock && (c.showCourseIds || []).length);
        if (!unlocked.length) { out.questions[s.userId] = { courseName: '', items: [] }; continue; }
        unlocked.sort((a, b) => (b.sort || 0) - (a.sort || 0));
        const top = unlocked[0];
        const det = await post(CRM + '/normalClass/all/question/detail/multiple', {
          courseIdList: top.showCourseIds,
          userId: s.userId,
          ojCloud: false
        });
        const data = (det && det.data) || {};
        const items = [];
        for (const key of Object.keys(data)) {
          const steps = data[key] || [];
          for (const step of steps) {
            for (const q of step.newCourseData || []) {
              const oj = (q.ojData && q.ojData.code) || '';
              items.push({
                course: key,
                stepName: step.stepName || '',
                name: q.name || '',
                type: q.type,
                description: String(q.description || '').replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 600),
                normalAnswer: q.normalAnswer || '',
                userAnswer: q.userAnswer || '',
                right: q.right,
                code: typeof oj === 'string' ? oj.slice(0, 3000) : '',
                knowledge: q.knowledgeArr || [],
                options: (q.options || []).map((o) => ({ text: o.text || '', correct: !!o.isCorrect, chosen: !!o.isChosen }))
              });
            }
          }
        }
        // 带代码的最相关，其次做过的，最后是还没做的
        items.sort((a, b) => {
          const rank = (x) => (x.code ? 0 : x.userAnswer ? 1 : 2);
          return rank(a) - rank(b);
        });
        out.questions[s.userId] = { courseName: top.courseName, courseIds: top.showCourseIds, items: items.slice(0, 40) };
      }
    }
    await Promise.all([qWorker(), qWorker()]);
  }

  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
if (r.exceptionDetails) {
  console.log("页面异常:", JSON.stringify(r.exceptionDetails).slice(0, 1200));
  process.exit(1);
}
const data = JSON.parse(r.result.value);
data.pageUrl = page.url;
fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
fs.writeFileSync(path.resolve(OUT), JSON.stringify(data, null, 2), "utf8");

const msgCount = Object.values(data.messages).reduce((a, m) => a + m.length, 0);
console.log(
  "学员:", data.students.length,
  "| 未读合计:", data.allUnreadCount,
  "| 待关注:", data.targetIds.length,
  "| 消息:", msgCount,
  "| 题目学员:", Object.keys(data.questions).length,
  "->", OUT
);
if (data.errors.length) console.log("错误:", data.errors.join(" | ").slice(0, 500));
ws.close();
process.exit(0);
