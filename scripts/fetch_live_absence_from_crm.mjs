import fs from "node:fs";
import path from "node:path";

function arg(name, fallback) {
  const idx = process.argv.indexOf(name);
  return idx >= 0 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

function hasFlag(name) {
  return process.argv.includes(name);
}

const port = Number(arg("--port", "9222"));
const courseNum = Number(arg("--course-num", "0"));
const days = Number(arg("--days", "14"));
const outJson = arg("--out-json", `data/live-course-${courseNum || "all"}-absent.json`);
const outCsv = arg("--out-csv", `data/live-course-${courseNum || "all"}-absent.csv`);
const dryRun = hasFlag("--dry-run");
const allStudents = hasFlag("--all-students");

if (!courseNum) {
  throw new Error("Missing --course-num, for example: --course-num 47");
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
  return res.json();
}

const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
const page =
  targets.find((target) => target.type === "page" && /codecamp-crm\.codemao\.cn/.test(target.url)) ||
  targets.find((target) => target.type === "page" && /codemao\.cn/.test(target.url));

if (!page?.webSocketDebuggerUrl) {
  throw new Error(`No logged-in CRM page found on Chrome debug port ${port}`);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;

function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`Timeout waiting for ${method}`));
      }
    }, 60000);
  });
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
});

function csvEscape(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function toLocalTime(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
}

const expression = `
(async () => {
  const courseNum = ${JSON.stringify(courseNum)};
  const days = ${JSON.stringify(days)};
  const allStudents = ${JSON.stringify(allStudents)};
  const now = Math.floor(Date.now() / 1000);
  const minLivingStartTime = now - days * 86400;
  const maxLivingStartTime = now + 3600;
  const headers = { "Content-Type": "application/json;charset=UTF-8", "authorization_type": "3" };
  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify(body)
    });
    const text = await response.text();
    let json;
    try { json = JSON.parse(text); } catch { throw new Error(text.slice(0, 500)); }
    if (!response.ok || json.code && json.code !== 200) {
      throw new Error(JSON.stringify({ status: response.status, url, body, response: json }).slice(0, 2000));
    }
    return json;
  }
  async function listBoards(page) {
    return post("https://lbk-crm-teacher-web-api.codemao.cn/shengwang/living/boards", {
      page,
      limit: 100,
      minLivingStartTime,
      maxLivingStartTime,
      livingTypes: [0]
    });
  }
  const first = await listBoards(1);
  const total = first.data?.total || 0;
  const pageSize = first.data?.pageSize || 100;
  let boards = first.data?.items || [];
  const pages = Math.ceil(total / pageSize);
  for (let page = 2; page <= pages; page++) {
    const next = await listBoards(page);
    boards = boards.concat(next.data?.items || []);
  }
  boards = boards.filter((board) => String(board.courseName || "").startsWith(courseNum + "-"));
  async function studentsByParticipation(roomUuid, pageIndex, isParticipated) {
    return post("https://cloud-gateway.codemao.cn/crm-common/shengwang/living/students", {
      roomUuid,
      pageIndex,
      pageSize: 100,
      isParticipated
    });
  }
  const rows = [];
  for (const board of boards) {
    const participationStates = allStudents ? [false, true] : [false];
    for (const isParticipated of participationStates) {
      const firstStudents = await studentsByParticipation(
        board.roomUuid,
        1,
        isParticipated
      );
      const studentTotal = firstStudents.data?.total || 0;
      let students = firstStudents.data?.items || [];
      const studentPages = Math.ceil(studentTotal / 100);
      for (let pageIndex = 2; pageIndex <= studentPages; pageIndex++) {
        const next = await studentsByParticipation(
          board.roomUuid,
          pageIndex,
          isParticipated
        );
        students = students.concat(next.data?.items || []);
      }
      for (const student of students) {
        rows.push({ board, student });
      }
    }
  }
  return JSON.stringify({
    courseNum,
    days,
    fetchedAt: new Date().toISOString(),
    boardCount: boards.length,
    rowCount: rows.length,
    boards,
    rows
  });
})()
`;

await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));
try {
  await send("Runtime.enable");
  const result = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  const parsed = JSON.parse(result.result.value);
  if (!dryRun) {
    fs.mkdirSync(path.dirname(path.resolve(outJson)), { recursive: true });
    fs.writeFileSync(outJson, JSON.stringify(parsed, null, 2), "utf8");
    const headers = [
      "记录时间",
      "课程周次",
      "直播课节",
      "直播ID",
      "直播标题",
      "直播开始时间",
      "直播类型",
      "班级名称",
      "班级ID",
      "学员ID",
      "学生姓名",
      "微信昵称",
      "是否到播",
      "直播观看时长秒",
      "总观看时长秒",
      "回放时长秒",
      "是否评论",
      "评论次数",
      "互动次数",
      "首次进入直播间时间",
      "最后离开直播间时间",
      "备注",
    ];
    const csvRows = [headers];
    for (const { board, student } of parsed.rows) {
      csvRows.push([
        toLocalTime(Date.now() / 1000),
        String(courseNum),
        board.courseName || "",
        board.livingId || "",
        board.roomName || "",
        toLocalTime(board.livingStartTime),
        board.livingBusinessTypeDesc || "",
        student.termClassName || (board.classNameList || []).join("，"),
        student.classId || (board.classIdList || []).join("，"),
        student.userId || "",
        student.studentName || "",
        student.wechatNickname || "",
        student.isParticipated ? "是" : "否",
        student.livingWatchDuration || 0,
        student.visitTotalDuration || 0,
        student.visitRecordEffectiveTime || 0,
        student.commentCount ? "是" : "否",
        student.commentCount || 0,
        student.interactionNum || 0,
        toLocalTime(student.livingFirstEnterTime),
        toLocalTime(student.livingLastExitTime),
        "",
      ]);
    }
    fs.writeFileSync(outCsv, csvRows.map((row) => row.map(csvEscape).join(",")).join("\n"), "utf8");
  }
  console.log(JSON.stringify({
    courseNum,
    days,
    boardCount: parsed.boardCount,
    rowCount: parsed.rowCount,
    outJson: dryRun ? null : outJson,
    outCsv: dryRun ? null : outCsv,
    boards: parsed.boards.reduce((acc, board) => {
      const key = `${board.livingId} ${board.courseName} ${board.classNameList?.[0] || ""}`;
      acc[key] = parsed.rows.filter((row) => row.board.livingId === board.livingId).length;
      return acc;
    }, {}),
  }, null, 2));
  process.exitCode = 0;
} finally {
  ws.close();
  setTimeout(() => process.exit(process.exitCode ?? 0), 50);
}
