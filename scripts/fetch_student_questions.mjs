import "./ws-shim.mjs";

// 只取单个学员「最新已解锁课程」的作答与 OJ 代码，供追答阶段使用。
// 用法: node fetch_student_questions.mjs --port=9223 --page=user-board --user-id=<学生> --term-id=<期次> --class-id=<班级>
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const USER_ID = Number(arg("--user-id", "0"));
const TERM_ID = Number(arg("--term-id", "0"));
const CLASS_ID = Number(arg("--class-id", "0"));

if (!USER_ID || !TERM_ID) {
  console.log(JSON.stringify({ ok: false, error: "缺少 --user-id 或 --term-id" }));
  process.exit(1);
}

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
  const get = async (u) => {
    try {
      const r = await fetch(u, { credentials: 'include' });
      const t = await r.text();
      try { return JSON.parse(t); } catch (e) { return { _status: r.status }; }
    } catch (e) { return { _status: 0, _err: String((e && e.message) || e) }; }
  };
  const post = async (u, b) => {
    try {
      const r = await fetch(u, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
      const t = await r.text();
      try { return JSON.parse(t); } catch (e) { return { _status: r.status }; }
    } catch (e) { return { _status: 0, _err: String((e && e.message) || e) }; }
  };

  const userId = ${USER_ID};
  const termId = ${TERM_ID};
  const classId = ${CLASS_ID};
  const out = { ok: true, userId: userId, termId: termId, courseName: '', items: [] };

  const uc = await get(CRM + '/inClassBoard/unlockCourse/' + termId + (classId ? '?classId=' + classId : ''));
  const courses = (uc && uc.data) || [];
  const unlocked = courses.filter((c) => c.unlock && (c.showCourseIds || []).length);
  if (!unlocked.length) { out.note = '没有已解锁的课程'; return JSON.stringify(out); }
  unlocked.sort((a, b) => (b.sort || 0) - (a.sort || 0));
  const top = unlocked[0];
  out.courseName = top.courseName || '';

  const det = await post(CRM + '/normalClass/all/question/detail/multiple', {
    courseIdList: top.showCourseIds,
    userId: userId,
    ojCloud: false
  });
  const data = (det && det.data) || {};
  const items = [];
  for (const key of Object.keys(data)) {
    for (const step of data[key] || []) {
      for (const q of step.newCourseData || []) {
        const oj = (q.ojData && q.ojData.code) || '';
        items.push({
          name: q.name || '',
          type: q.type,
          description: String(q.description || '').replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 600),
          userAnswer: q.userAnswer || '',
          right: q.right,
          code: typeof oj === 'string' ? oj.slice(0, 3000) : '',
          knowledge: q.knowledgeArr || [],
          options: (q.options || []).map((o) => ({ text: o.text || '', correct: !!o.isCorrect, chosen: !!o.isChosen }))
        });
      }
    }
  }
  items.sort((a, b) => {
    const rank = (x) => (x.code ? 0 : x.userAnswer ? 1 : 2);
    return rank(a) - rank(b);
  });
  out.items = items.slice(0, 40);
  return JSON.stringify(out);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
if (r.exceptionDetails) {
  console.log(JSON.stringify({ ok: false, error: "页面异常", detail: JSON.stringify(r.exceptionDetails).slice(0, 300) }));
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
