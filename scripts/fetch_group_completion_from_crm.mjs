import fs from "node:fs";
import path from "node:path";

function arg(name, fallback) {
  const idx = process.argv.indexOf(name);
  return idx >= 0 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const port = Number(arg("--port", "9222"));
const inputCsv = arg("--classes-csv", "data/superset-dashboard-117-teacher-class-detail.csv");
const outJson = arg("--out-json", "data/group-student-completion-detail.json");
const outCsv = arg("--out-csv", "data/group-student-completion-detail.csv");
const pageLimit = Number(arg("--page-limit", "100"));

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  const input = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (quoted) {
      if (ch === '"' && input[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  if (cell || row.length) {
    row.push(cell.replace(/\r$/, ""));
    rows.push(row);
  }
  const headers = rows.shift() || [];
  return rows.filter((item) => item.length).map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

function csvEscape(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function toLocalTime(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
  return res.json();
}

const classRows = parseCsv(fs.readFileSync(inputCsv, "utf8"));
const classTargets = classRows
  .map((row) => ({
    teacherName: row.beisen_user_fullname,
    workerNo: row.worker_no,
    teacherEmail: row.teacher_email,
    classId: Number(row.class_id),
    className: row.class_name,
    termId: Number(row.term_id),
    termName: row.term_name,
    packageId: Number(row.package_id),
    packageName: row.package_name,
    currentCourseSort: row.current_new_course_sort,
    currentCourseName: row.current_new_course_name,
    currentUserCount: row.current_user_cnt,
    renewDenominatorCount: row.renew_denominator_cnt,
    level5DepartmentName: row.level_5_department_name,
    level6DepartmentName: row.level_6_department_name,
    level7DepartmentName: row.level_7_department_name,
  }))
  .filter((row) => row.classId && row.termId);

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
    }, 120000);
  });
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
});

await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));
await send("Runtime.enable");

async function evaluateJson(expression) {
  const result = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails).slice(0, 2000));
  }
  return JSON.parse(result.result.value);
}

async function fetchClass(classInfo) {
  const expression = `
(async () => {
  const classInfo = ${JSON.stringify(classInfo)};
  const pageLimit = ${JSON.stringify(pageLimit)};
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

  const baseBody = {
    class_id: classInfo.classId,
    term_id: classInfo.termId,
    new_stage_paper_send_status_stage: 5
  };
  const first = await post("https://api-codecamp-crm.codemao.cn/annual/class/overview?page=1&limit=" + pageLimit, baseBody);
  const total = first.total || 0;
  const items = [...(first.items || [])];
  const pages = Math.ceil(total / pageLimit);
  for (let page = 2; page <= pages; page++) {
    const next = await post("https://api-codecamp-crm.codemao.cn/annual/class/overview?page=" + page + "&limit=" + pageLimit, baseBody);
    items.push(...(next.items || []));
  }
  return JSON.stringify({ classInfo, total, fetched: items.length, rows: items.map((student) => ({ classInfo, student })) });
})()
`;
  return evaluateJson(expression);
}

const summaries = [];
const rows = [];
for (let index = 0; index < classTargets.length; index++) {
  const classInfo = classTargets[index];
  const result = await fetchClass(classInfo);
  summaries.push({ ...classInfo, total: result.total, fetched: result.fetched });
  rows.push(...result.rows);
  console.log(
    JSON.stringify(
      {
        progress: `${index + 1}/${classTargets.length}`,
        teacherName: classInfo.teacherName,
        classId: classInfo.classId,
        className: classInfo.className,
        termId: classInfo.termId,
        fetched: result.fetched,
        total: result.total,
      },
      null,
      0,
    ),
  );
}

const parsed = {
  fetchedAt: new Date().toISOString(),
  classCount: classTargets.length,
  rowCount: rows.length,
  summaries,
  rows,
};
fs.mkdirSync(path.dirname(path.resolve(outJson)), { recursive: true });
fs.writeFileSync(outJson, JSON.stringify(parsed, null, 2), "utf8");

const headers = [
  "????",
  "??",
  "??ID",
  "???",
  "??ID",
  "???",
  "???",
  "??????",
  "?????",
  "?????",
  "????",
  "??ID",
  "????",
  "??",
  "??",
  "????????",
  "???????",
  "??????",
  "??????",
  "???",
  "?????",
  "?????",
  "??/?????",
  "?????",
  "?????",
  "??????",
  "???"
];

const fetchedAt = toLocalTime(Date.now() / 1000);
const csvRows = [headers];
for (const { classInfo, student } of parsed.rows) {
  const nFinish = Number(student.n_finish || 0);
  const nOpen = Number(student.n_open || 0);
  csvRows.push([
    fetchedAt,
    classInfo.teacherName,
    classInfo.classId,
    classInfo.className,
    classInfo.termId,
    classInfo.termName,
    classInfo.packageName,
    classInfo.currentCourseSort,
    classInfo.currentCourseName,
    classInfo.currentUserCount,
    classInfo.renewDenominatorCount,
    student.user_id,
    student.child_name,
    student.sex,
    student.age,
    student.course_number,
    student.course_name,
    student.n_finish,
    student.n_open,
    nOpen ? (nFinish / nOpen).toFixed(4) : "",
    student.n_complete_work,
    student.n_upload_video,
    student.n_weekly_test,
    student.n_answer_in_class,
    student.n_answer_after_class,
    student.duration_in_class,
    student.group_name,
  ]);
}

fs.writeFileSync(outCsv, `\uFEFF${csvRows.map((row) => row.map(csvEscape).join(",")).join("\n")}`, "utf8");
console.log(JSON.stringify({
  classCount: parsed.classCount,
  rowCount: parsed.rowCount,
  outJson,
  outCsv,
  summaries: parsed.summaries.map((item) => ({
    teacherName: item.teacherName,
    classId: item.classId,
    className: item.className,
    termId: item.termId,
    termName: item.termName,
    total: item.total,
    fetched: item.fetched
  }))
}, null, 2));
