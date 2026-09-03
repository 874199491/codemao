import fs from "node:fs";
import path from "node:path";

function arg(name, fallback) {
  const idx = process.argv.indexOf(name);
  return idx >= 0 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const port = Number(arg("--port", "9222"));
const userId = Number(arg("--user-id", ""));
const courseId = Number(arg("--course-id", "0"));
const outJson = arg("--out-json", "data/single-student-questions.json");

if (!userId) {
  throw new Error("Missing --user-id");
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

const expression = `
(async () => {
  const userId = ${JSON.stringify(userId)};
  const courseId = ${JSON.stringify(courseId)};
  const headers = { "Content-Type": "application/json;charset=UTF-8" };
  async function post(url, body) {
    const response = await fetch(url, { method: "POST", headers, credentials: "include", body: JSON.stringify(body) });
    const text = await response.text();
    let json;
    try { json = JSON.parse(text); } catch { throw new Error(text.slice(0, 500)); }
    if (!response.ok) throw new Error(JSON.stringify({ status: response.status, url, body, response: json }).slice(0, 2000));
    return json;
  }
  const courseIdList = courseId > 0 ? [courseId] : [];
  const response = await post("https://cloud-gateway.codemao.cn/crm-rocket/normalClass/all/question/detail/multiple", {
    courseIdList,
    userId,
    ojCloud: false
  });
  return JSON.stringify({ userId, courseId: courseId || null, data: response.data || {} });
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
  fs.mkdirSync(path.dirname(path.resolve(outJson)), { recursive: true });
  fs.writeFileSync(outJson, JSON.stringify(parsed, null, 2), "utf8");
  console.log(JSON.stringify({ userId: parsed.userId, courseId: parsed.courseId, outJson }, null, 2));
  process.exitCode = 0;
} finally {
  ws.close();
  setTimeout(() => process.exit(process.exitCode ?? 0), 50);
}
