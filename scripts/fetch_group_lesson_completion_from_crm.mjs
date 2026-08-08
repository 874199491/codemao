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
const classesCsv = arg("--classes-csv", "data/superset-dashboard-117-teacher-class-detail.csv");
const studentsJson = arg("--students-json", "data/group-student-completion-detail.json");
const outJson = arg("--out-json", "data/group-student-lesson-completion.json");
const outCsv = arg("--out-csv", "data/group-student-lesson-completion.csv");
const pageLimit = Number(arg("--page-limit", "500"));
const runtimeTimeoutMs = Number(arg("--runtime-timeout-ms", "600000"));
const targetMaxLesson = Number(arg("--max-lesson", "0"));
const reuseJson = hasFlag("--reuse-json");

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

const classRows = parseCsv(fs.readFileSync(classesCsv, "utf8"));
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
    currentCourseSort: Number(row.current_new_course_sort),
    currentCourseName: row.current_new_course_name,
    currentUserCount: row.current_user_cnt,
    renewDenominatorCount: row.renew_denominator_cnt,
    level5DepartmentName: row.level_5_department_name,
    level6DepartmentName: row.level_6_department_name,
    level7DepartmentName: row.level_7_department_name,
  }))
  .filter((row) => row.classId && row.termId)
  .map((row) => ({
    ...row,
    currentCourseSort:
      targetMaxLesson > 0
        ? Math.max(row.currentCourseSort || 0, targetMaxLesson)
        : row.currentCourseSort,
  }));

const studentSnapshot = JSON.parse(fs.readFileSync(studentsJson, "utf8"));
const studentBaseByClass = new Map();
for (const row of studentSnapshot.rows || []) {
  const classInfo = row.classInfo || {};
  const student = row.student || {};
  const key = `${classInfo.termId}:${classInfo.classId}`;
  if (!studentBaseByClass.has(key)) studentBaseByClass.set(key, new Map());
  studentBaseByClass.get(key).set(String(student.user_id), { classInfo, student });
}

let ws = null;
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
    }, runtimeTimeoutMs);
  });
}

if (!reuseJson) {
  const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
  const page =
    targets.find((target) => target.type === "page" && /codecamp-crm\.codemao\.cn/.test(target.url)) ||
    targets.find((target) => target.type === "page" && /codemao\.cn/.test(target.url));

  if (!page?.webSocketDebuggerUrl) {
    throw new Error(`No logged-in CRM page found on Chrome debug port ${port}`);
  }

  ws = new WebSocket(page.webSocketDebuggerUrl);
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
  });

  await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));
  await send("Runtime.enable");
}

async function evaluateJson(expression) {
  const result = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails).slice(0, 4000));
  }
  if (result.result?.subtype === "error") {
    throw new Error(result.result.description || JSON.stringify(result.result).slice(0, 4000));
  }
  return JSON.parse(result.result.value);
}

async function fetchClassLessons(classInfo) {
  if (classInfo.currentCourseSort < 1) {
    return { classInfo, total: 0, fetched: 0, rows: [], skipped: "当前未开课或课程序号未知" };
  }
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
    if (!response.ok) {
      throw new Error(JSON.stringify({ status: response.status, url, body, response: json }).slice(0, 2000));
    }
    return json;
  }

  function body() {
    return {
      term_id: classInfo.termId,
      class_id: classInfo.classId,
      time_type: 1,
      study_report_feedback_grade: "",
      work_level: "",
      study_report_sending_status: "",
      report_event_flag: "",
      watch_process_range_list: [],
      stay_time_range_list: [],
      node_status: 0,
      node_course_id_list: [],
      node_id_list: [],
      courseName: "",
      oj_question_state: 0,
      week_test_oj_status: 0,
      queryType: 1,
      nct_user: "",
      send_video_comments: "",
      send_photo_comments: ""
    };
  }

  const first = await post("https://api-codecamp-crm.codemao.cn/annual/class/course-detail?page=1&limit=" + pageLimit, body());
  const total = first.total || 0;
  const items = [...(first.items || [])];
  const pages = Math.ceil(total / pageLimit);
  for (let page = 2; page <= pages; page++) {
    const next = await post("https://api-codecamp-crm.codemao.cn/annual/class/course-detail?page=" + page + "&limit=" + pageLimit, body());
    items.push(...(next.items || []));
  }
  const seen = new Set();
  const deduped = [];
  for (const item of items) {
    const key = [item.user_id, item.course_id, item.no_free_sort || item.course_number || ""].join(":");
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }
  return JSON.stringify({
    classInfo,
    total,
    fetched: deduped.length,
    rawFetched: items.length,
    rows: deduped.map((item) => ({
      user_id: item.user_id,
      child_name: item.child_name,
      nickname: item.nickname,
      course_id: item.course_id,
      course_number: item.course_number,
      no_free_sort: item.no_free_sort,
      course_name: item.course_name,
      is_open: item.is_open,
      is_finish: item.is_finish,
      course_open_time: item.course_open_time,
      course_finish_time: item.course_finish_time
    }))
  });
})()
`;
  return evaluateJson(expression);
}

let classResults = [];
let previousClassResults = new Map();
if (fs.existsSync(outJson)) {
  try {
    const previous = JSON.parse(fs.readFileSync(outJson, "utf8"));
    previousClassResults = new Map((previous.classResults || []).map((item) => [`${item.classInfo?.termId}:${item.classInfo?.classId}`, item]));
  } catch {
    previousClassResults = new Map();
  }
}
if (reuseJson) {
  classResults = JSON.parse(fs.readFileSync(outJson, "utf8")).classResults || [];
  console.log(JSON.stringify({ reuseJson: outJson, classCount: classResults.length }, null, 0));
} else {
  for (let index = 0; index < classTargets.length; index++) {
    const classInfo = classTargets[index];
    let result;
    try {
      result = await fetchClassLessons(classInfo);
    } catch (error) {
      const key = `${classInfo.termId}:${classInfo.classId}`;
      const previous = previousClassResults.get(key);
      const message = error?.message || String(error);
      if (previous) {
        result = { ...previous, classInfo, skipped: `fetch_failed_used_previous: ${message.slice(0, 500)}` };
      } else {
        result = { classInfo, total: 0, fetched: 0, rows: [], skipped: `fetch_failed_no_previous: ${message.slice(0, 500)}` };
      }
    }
    classResults.push(result);
    console.log(
      JSON.stringify(
        {
          progress: `${index + 1}/${classTargets.length}`,
          teacherName: classInfo.teacherName,
          className: classInfo.className,
          termId: classInfo.termId,
          classId: classInfo.classId,
          currentCourseSort: classInfo.currentCourseSort,
          fetched: result.fetched,
          total: result.total,
          skipped: result.skipped || "",
        },
        null,
        0,
      ),
    );
  }
}

const maxLesson = Math.max(0, ...classTargets.map((row) => (row.currentCourseSort > 0 ? row.currentCourseSort : 0)));
const lessonColumns = Array.from({ length: maxLesson }, (_, index) => `第${index + 1}课`);
const fetchedAt = toLocalTime(Date.now() / 1000);
const headers = [
  "抓取时间",
  "老师",
  "课程包",
  "班级ID",
  "班级名",
  "期次ID",
  "期次名",
  "当前课程序号",
  "当前课程名",
  "学生ID",
  "学生姓名",
  "已开放课节数",
  "已完课课节数",
  "完课率",
  ...lessonColumns
];

const csvRows = [headers];
const detailRows = [];
const summaries = [];

function lessonSort(item, classInfo) {
  const name = String(item.course_name || "");
  const match = name.match(/^(\d+)[-－]/);
  const fromName = match ? Number(match[1]) : 0;
  const raw = Number(item.no_free_sort || item.course_number || 0);
  if (fromName > 0 && fromName <= classInfo.currentCourseSort) return fromName;
  if (Number.isFinite(raw) && raw > 0 && raw <= classInfo.currentCourseSort) return raw;
  return fromName || (Number.isFinite(raw) && raw > 0 ? raw : 0);
}

for (const classResult of classResults) {
  const classInfo = classResult.classInfo;
  const classKey = `${classInfo.termId}:${classInfo.classId}`;
  const students = new Map(studentBaseByClass.get(classKey) || []);
  const lessonByUser = new Map();
  const lessonNames = new Map();

  for (const item of classResult.rows || []) {
    const sort = lessonSort(item, classInfo);
    if (!sort || sort > maxLesson) continue;
    lessonNames.set(sort, item.course_name || "");
    const userId = String(item.user_id);
    if (!students.has(userId)) {
      students.set(userId, { classInfo, student: { user_id: item.user_id, child_name: item.child_name } });
    }
    if (!lessonByUser.has(userId)) lessonByUser.set(userId, new Map());
    const status = item.is_open === false ? "未完课" : item.is_finish ? "已完课" : "到课未完课";
    lessonByUser.get(userId).set(sort, {
      status,
      courseId: item.course_id,
      courseName: item.course_name || "",
      openTime: item.course_open_time || "",
      finishTime: item.course_finish_time || "",
    });
  }

  let classStudentCount = 0;
  let classOpened = 0;
  let classFinished = 0;
  for (const { student } of students.values()) {
    const userId = String(student.user_id);
    const lessons = lessonByUser.get(userId) || new Map();
    let opened = 0;
    let finished = 0;
    const statuses = [];
    for (let sort = 1; sort <= maxLesson; sort++) {
      const item = lessons.get(sort);
      let status = "";
      if (sort <= classInfo.currentCourseSort) {
        status = item?.status || "无数据";
      }
      if (status === "已完课" || status === "到课未完课") opened++;
      if (status === "已完课") finished++;
      statuses.push(status);
      if (status) {
        detailRows.push({
          teacherName: classInfo.teacherName,
          classId: classInfo.classId,
          className: classInfo.className,
          termId: classInfo.termId,
          termName: classInfo.termName,
          userId,
          childName: student.child_name,
          lessonSort: sort,
          lessonName: item?.courseName || lessonNames.get(sort) || "",
          status,
          openTime: item?.openTime || "",
          finishTime: item?.finishTime || "",
        });
      }
    }
    classStudentCount++;
    classOpened += opened;
    classFinished += finished;
    csvRows.push([
      fetchedAt,
      classInfo.teacherName,
      classInfo.packageName,
      classInfo.classId,
      classInfo.className,
      classInfo.termId,
      classInfo.termName,
      classInfo.currentCourseSort > 0 ? classInfo.currentCourseSort : "",
      classInfo.currentCourseName === "??" ? "" : classInfo.currentCourseName,
      userId,
      student.child_name,
      opened,
      finished,
      opened ? (finished / opened).toFixed(4) : "",
      ...statuses,
    ]);
  }
  summaries.push({
    teacherName: classInfo.teacherName,
    classId: classInfo.classId,
    className: classInfo.className,
    termId: classInfo.termId,
    studentCount: classStudentCount,
    openedLessonCells: classOpened,
    finishedLessonCells: classFinished,
    completionRate: classOpened ? Number((classFinished / classOpened).toFixed(4)) : null,
    fetchedRows: classResult.fetched || 0,
    skipped: classResult.skipped || "",
  });
}

const parsed = {
  fetchedAt: new Date().toISOString(),
  classCount: classTargets.length,
  studentRowCount: csvRows.length - 1,
  detailRowCount: detailRows.length,
  maxLesson,
  summaries,
  classResults,
  detailRows,
};
fs.mkdirSync(path.dirname(path.resolve(outJson)), { recursive: true });
fs.writeFileSync(outJson, JSON.stringify(parsed, null, 2), "utf8");
fs.writeFileSync(outCsv, `\uFEFF${csvRows.map((row) => row.map(csvEscape).join(",")).join("\n")}`, "utf8");
console.log(JSON.stringify({ outJson, outCsv, classCount: parsed.classCount, studentRowCount: parsed.studentRowCount, detailRowCount: parsed.detailRowCount, maxLesson, summaries }, null, 2));
if (ws) ws.close();
setTimeout(() => process.exit(0), 50);
