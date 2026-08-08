import fs from "node:fs";
import path from "node:path";

const workspace = process.cwd();
const workbenchConfigPath = path.join(workspace, "data", "teacher-workbench-config.json");
const sendConfigPath = path.join(workspace, "data", "new-class-group-send-cancel-config.json");
const workbenchConfig = JSON.parse(fs.readFileSync(workbenchConfigPath, "utf8"));
const sendConfig = JSON.parse(fs.readFileSync(sendConfigPath, "utf8"));
const port = Number(workbenchConfig.chrome_debug_port || 9223);
const profile = workbenchConfig.profile || {};
const files = profile.files || {};
const classPoolId = Number(
  profile.crm?.class_pool_id ||
  workbenchConfig.crm?.class_pool_id ||
  sendConfig.class_pool_id ||
  0
);

if (!classPoolId) {
  throw new Error(
    "Missing class_pool_id. Please set data/new-class-group-send-cancel-config.json class_pool_id, " +
    "or profile.crm.class_pool_id in data/teacher-workbench-config.json."
  );
}

const activeOut = files.students_json || `data/${profile.data_prefix || "demo"}-student-completion-detail.json`;
const refundedOut = files.refunded_json || `data/${profile.data_prefix || "demo"}-refunded-students.json`;

const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = targets.find((t) => t.type === "page" && /codecamp-crm\.codemao\.cn/.test(t.url));
if (!page) throw new Error(`No CRM page on Chrome debug port ${port}`);

const ws = new WebSocket(page.webSocketDebuggerUrl);
let seq = 0;
const pending = new Map();

function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`timeout ${method}`));
      }
    }, 120000);
  });
}

ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const p = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
  }
});

await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));

const cookieResult = await send("Storage.getCookies");
const cookies = cookieResult.cookies || [];
const byName = Object.fromEntries(cookies.map((cookie) => [cookie.name, cookie.value]));
const cookieHeader = [
  "__ca_uid_key__",
  "internal_account_token",
  "admin-authorization",
  "sensorsdata2015jssdkcross",
]
  .filter((name) => byName[name])
  .map((name) => `${name}=${byName[name]}`)
  .join("; ");
const authHeader = byName["admin-authorization"] || "";

async function fetchStudents(afterSaleStatus) {
  const response = await fetch("https://lbk-crm-teacher-web-api.codemao.cn/classShiftPool/users", {
    method: "POST",
    headers: {
      "Content-Type": "application/json;charset=UTF-8",
      authorization_type: "3",
      Cookie: cookieHeader,
      Origin: "https://codecamp-crm.codemao.cn",
      Referer: "https://codecamp-crm.codemao.cn/layout/step/index",
      ...(authHeader ? { Authorization: authHeader.startsWith("Bearer") ? authHeader : `Bearer ${authHeader}` } : {}),
    },
    body: JSON.stringify({
      page: 1,
      limit: 500,
      classPoolId,
      afterSaleStatus,
    }),
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(text.slice(0, 2000));
  }
  return JSON.parse(text);
}

try {
  const active = await fetchStudents(0);
  const refunded = await fetchStudents(6);
  fs.mkdirSync(path.dirname(path.resolve(activeOut)), { recursive: true });
  fs.mkdirSync(path.dirname(path.resolve(refundedOut)), { recursive: true });
  fs.writeFileSync(activeOut, JSON.stringify(active, null, 2), "utf8");
  fs.writeFileSync(refundedOut, JSON.stringify(refunded, null, 2), "utf8");
  console.log(JSON.stringify({
    classPoolId,
    activeOut,
    count: active?.data?.items?.length || 0,
    total: active?.data?.total || active?.data?.count || null,
    refundedOut,
    refundedCount: refunded?.data?.items?.length || 0,
  }, null, 2));
} finally {
  ws.close();
  setTimeout(() => process.exit(0), 50);
}
