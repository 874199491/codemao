import fs from "node:fs";
import path from "node:path";

function arg(name, fallback) {
  const idx = process.argv.indexOf(name);
  return idx >= 0 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const port = Number(arg("--port", "9222"));
const userId = Number(arg("--user-id", ""));
const outDir = arg("--out-dir", "data/parent-chats");
const limit = Number(arg("--limit", "50"));
const months = Number(arg("--months", "0"));
const days = Number(arg("--days", "0"));

if (!userId) {
  throw new Error("Missing --user-id");
}

function formatLocalTime(ms) {
  if (!ms) return "";
  const date = new Date(ms);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function addMonths(date, delta) {
  const copy = new Date(date.getTime());
  copy.setMonth(copy.getMonth() + delta);
  return copy;
}

function addDays(date, delta) {
  const copy = new Date(date.getTime());
  copy.setDate(copy.getDate() + delta);
  return copy;
}

function safeWriteFile(filePath, content) {
  try {
    fs.writeFileSync(filePath, content, "utf8");
    return filePath;
  } catch (error) {
    if (error?.code !== "EBUSY") throw error;
    const parsed = path.parse(filePath);
    const fallback = path.join(parsed.dir, `${parsed.name}-${new Date().toISOString().replace(/[:.]/g, "-")}${parsed.ext}`);
    fs.writeFileSync(fallback, content, "utf8");
    return fallback;
  }
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
  const limit = ${JSON.stringify(limit)};
  const businessCode = "lbk-crm-teacher-web-api";
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
    if (!response.ok || json.success === false) {
      throw new Error(JSON.stringify({ status: response.status, url, body, response: json }).slice(0, 2000));
    }
    return json;
  }

  const userSearch = await post("https://codecamp-marketing.codemao.cn/session/user/search", {
    userWechatIds: [],
    phone: "",
    userId: String(userId),
    limit: 20,
    page: 1,
    businessCode
  });
  const wechatUsers = userSearch?.data?.items || [];

  const conversations = [];
  for (const wechatUser of wechatUsers) {
    const selectedWechatIds = wechatUser.userWechatIds || [wechatUser.userWechatId].filter(Boolean);
    const empSearch = await post("https://codecamp-marketing.codemao.cn/session/emp/search/list", {
      userWechatId: wechatUser.userWechatId,
      selectedWechatIds,
      page: 1,
      limit: 20,
      systemCode: businessCode,
      userId,
      empName: ""
    });
    const empUsers = empSearch?.data?.items || [];

    const groupSearch = await post("https://codecamp-marketing.codemao.cn/chat/session/search/list", {
      businessCode,
      userWechatId: wechatUser.userWechatId,
      selectedWechatIds,
      userId,
      chatId: "",
      page: 1,
      limit: 50
    });

    for (const empUser of empUsers) {
      const single = await post("https://codecamp-marketing.codemao.cn/single/session/search", {
        empWechatId: empUser.empWechatId,
        userWechatId: wechatUser.userWechatId,
        searchContent: "",
        businessCode,
        searchType: "0",
        msgId: "",
        limit: String(limit),
        corpId: wechatUser.corpId,
        direction: ""
      });
      conversations.push({
        wechatUser,
        empUser,
        groups: groupSearch?.data?.items || [],
        messages: single?.data || []
      });
    }
  }

  return JSON.stringify({
    fetchedAt: new Date().toISOString(),
    userId,
    businessCode,
    wechatUserCount: wechatUsers.length,
    conversationCount: conversations.length,
    conversations
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
  const value = result.result?.value;
  const parsed = typeof value === "string" ? JSON.parse(value) : value;
  if (days > 0) {
    const cutoff = addDays(new Date(), -days).getTime();
    parsed.filter = { days, cutoffTime: cutoff, cutoffLocalTime: formatLocalTime(cutoff) };
    for (const conversation of parsed.conversations || []) {
      conversation.rawMessageCount = (conversation.messages || []).length;
      conversation.messages = (conversation.messages || []).filter((message) => !message.msgTime || message.msgTime >= cutoff);
      conversation.filteredMessageCount = conversation.messages.length;
    }
  } else if (months > 0) {
    const cutoff = addMonths(new Date(), -months).getTime();
    parsed.filter = { months, cutoffTime: cutoff, cutoffLocalTime: formatLocalTime(cutoff) };
    for (const conversation of parsed.conversations || []) {
      conversation.rawMessageCount = (conversation.messages || []).length;
      conversation.messages = (conversation.messages || []).filter((message) => !message.msgTime || message.msgTime >= cutoff);
      conversation.filteredMessageCount = conversation.messages.length;
    }
  }

  const studentDir = path.resolve(outDir, String(userId));
  fs.mkdirSync(studentDir, { recursive: true });
  const latestJson = path.join(studentDir, "latest.json");
  const snapshotJson = path.join(studentDir, `chat-${new Date().toISOString().replace(/[:.]/g, "-")}.json`);
  safeWriteFile(latestJson, JSON.stringify(parsed, null, 2));
  safeWriteFile(snapshotJson, JSON.stringify(parsed, null, 2));

  const csvRows = [["userId", "userWechatId", "empWechatId", "msgId", "messageId", "msgTime", "sender", "msgType", "content", "filePath", "senderName", "sensitiveMessage"]];
  for (const conversation of parsed.conversations || []) {
    for (const message of conversation.messages || []) {
      const msgTime = formatLocalTime(message.msgTime);
      const sender = message.flag === 0 ? "家长" : message.flag === 1 ? "老师" : "";
      const senderName = message.userWechatName || message.teacherNickName || message.teacherName || "";
      csvRows.push([
        parsed.userId,
        conversation.wechatUser?.userWechatId || "",
        conversation.empUser?.empWechatId || "",
        message.msgId || "",
        message.messageId || "",
        msgTime,
        sender,
        message.msgType || message.type || "",
        message.content || "",
        message.filePath || "",
        senderName,
        String(Boolean(message.sensitiveMessage)),
      ]);
    }
  }
  const csv = csvRows
    .map((row) => row.map((value) => '"' + String(value).replaceAll('"', '""') + '"').join(","))
    .join("\n");
  const csvPath = safeWriteFile(path.join(studentDir, "latest-messages.csv"), "\ufeff" + csv + "\n");

  console.log(JSON.stringify({
    userId,
    wechatUserCount: parsed.wechatUserCount,
    conversationCount: parsed.conversationCount,
    messageCount: csvRows.length - 1,
    latestJson,
    latestCsv: csvPath,
    snapshotJson,
  }, null, 2));
  process.exitCode = 0;
} finally {
  ws.close();
  setTimeout(() => process.exit(process.exitCode ?? 0), 50);
}
