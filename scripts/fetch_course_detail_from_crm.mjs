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
const courseNum = Number(arg("--course-num", "48"));
const courseId = Number(arg("--course-id", String(9725 + courseNum)));
const courseName = arg("--course-name", "");
const includeQuestions = hasFlag("--include-questions");
const classFile = arg("--class-file", "");
const outJson = arg("--out-json", `data/course-${courseNum}-detail.json`);

function parseCsvLine(line) {
  const cells = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted && ch === '"' && line[i + 1] === '"') {
      cell += '"';
      i++;
    } else if (ch === '"') {
      quoted = !quoted;
    } else if (ch === "," && !quoted) {
      cells.push(cell);
      cell = "";
    } else {
      cell += ch;
    }
  }
  cells.push(cell);
  return cells.map((value) => value.replace(/^\uFEFF/, "").trim());
}

function loadClassesFromCsv(file) {
  if (!file) throw new Error("Missing --class-file");
  const text = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) throw new Error(`No class rows in ${file}`);
  const headers = parseCsvLine(lines[0]);
  const rows = lines.slice(1).map((line) => {
    const cells = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, cells[index] ?? ""]));
  });
  const classes = rows
    .map((row) => {
      const classId = Number(row.class_id || row.classId);
      const termId = Number(row.term_id || row.termId);
      const name = row.class_name || row.className || row.term_name || row.termName || String(classId);
      return { name, term_id: termId, class_id: classId };
    })
    .filter((row) => row.class_id > 0 && row.term_id > 0);
  if (!classes.length) throw new Error(`No valid class_id/term_id rows in ${file}`);
  return classes;
}

const classes = loadClassesFromCsv(classFile);

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
    }, 600000);
  });
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
});

const expression = `
(async () => {
  const courseNum = ${JSON.stringify(courseNum)};
  const courseId = ${JSON.stringify(courseId)};
  const courseName = ${JSON.stringify(courseName)};
  const classes = ${JSON.stringify(classes)};
  const includeQuestions = ${JSON.stringify(includeQuestions)};

  const headers = { "Content-Type": "application/json;charset=UTF-8" };
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

  const detailRows = [];
  for (const classInfo of classes) {
    async function fetchPage(page) {
      return post("https://api-codecamp-crm.codemao.cn/annual/class/course-detail?page=" + page + "&limit=100", {
        term_id: classInfo.term_id,
        class_id: classInfo.class_id,
        ...(courseId > 0 ? { course_ids: [courseId] } : {}),
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
        courseName,
        oj_question_state: 0,
        week_test_oj_status: 0,
        queryType: 1,
        nct_user: "",
        send_video_comments: "",
        send_photo_comments: ""
      });
    }
    const first = await fetchPage(1);
    const total = first.total || 0;
    const limit = first.limit || 100;
    let items = first.items || [];
    const pages = Math.ceil(total / limit);
    for (let page = 2; page <= pages; page++) {
      const next = await fetchPage(page);
      items = items.concat(next.items || []);
    }
    const courseItems = courseId > 0 ? items : items.filter((item) => {
      const nameMatch = String(item.course_name || "").match(/^(\\d+)[-－]/);
      const fromName = nameMatch ? Number(nameMatch[1]) : 0;
      const sort = Number(item.no_free_sort || item.course_number || fromName || 0);
      return sort === courseNum || fromName === courseNum;
    });
    for (const item of courseItems) {
      item.class_display_name = classInfo.name;
      detailRows.push(item);
    }
  }

  const questionDetails = {};
  if (includeQuestions) {
    for (const row of detailRows) {
      const hasWork = (row.regular_question_finish_count || 0) > 0 || (row.oj_question_finish_count || 0) > 0 || (row.study_report_total_question_count || 0) > 0;
      if (!hasWork) continue;
      const response = await post("https://cloud-gateway.codemao.cn/crm-rocket/normalClass/all/question/detail/multiple", {
        courseIdList: [Number(row.course_id)],
        userId: row.user_id,
        ojCloud: false
      });
      questionDetails[String(row.user_id)] = response.data || {};
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
  }

  return JSON.stringify({
    fetchedAt: new Date().toISOString(),
    courseNum,
    courseId: courseId > 0 ? courseId : Number(detailRows[0]?.course_id || 0),
    classes,
    detailCount: detailRows.length,
    questionUserCount: Object.keys(questionDetails).length,
    detailRows,
    questionDetails
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
  if (result.exceptionDetails) {
    throw new Error(`Runtime exception: ${JSON.stringify(result.exceptionDetails).slice(0, 4000)}`);
  }
  if (result.result?.subtype === "error") {
    throw new Error(`Runtime error: ${result.result.description || JSON.stringify(result.result).slice(0, 4000)}`);
  }
  const value = result.result.value;
  const parsed = typeof value === "string" ? JSON.parse(value) : value;
  if (!parsed?.detailRows) {
    throw new Error(`Unexpected Runtime.evaluate result: ${JSON.stringify(result).slice(0, 4000)}`);
  }
  fs.mkdirSync(path.dirname(path.resolve(outJson)), { recursive: true });
  fs.writeFileSync(outJson, JSON.stringify(parsed, null, 2), "utf8");
  console.log(JSON.stringify({
    courseNum,
    courseId: parsed.courseId,
    detailCount: parsed.detailCount,
    questionUserCount: parsed.questionUserCount,
    outJson,
    byClass: parsed.detailRows.reduce((acc, row) => {
      const key = `${row.class_display_name}/${row.term_name}/${row.class_id}`;
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {}),
  }, null, 2));
  process.exitCode = 0;
} finally {
  ws.close();
  setTimeout(() => process.exit(process.exitCode ?? 0), 50);
}
