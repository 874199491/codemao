import fs from "node:fs";
import path from "node:path";

function arg(name, fallback = "") {
  const value = process.argv.find((item) => item.startsWith(`--${name}=`));
  return value ? value.slice(name.length + 3) : fallback;
}

const port = Number(arg("port", "9223"));
const classCode = arg("class-code", "0724");
const groupKeyword = arg("group-keyword", "周五19点");
const sinceText = arg("since");
const untilText = arg("until");
const outDir = path.resolve(arg("out-dir", "data/group-solitaire"));
const rosterPath = path.resolve(arg("roster", "data/new-class-student-list.json"));
const normalizedClassCode = String(classCode || "").trim();

if (!normalizedClassCode) {
  throw new Error(
    "Missing --class-code. Solitaire group search must include a cohort/class code, otherwise unrelated teacher groups may be captured."
  );
}

if (!sinceText) {
  throw new Error("Missing --since=<ISO date/time>");
}

const since = new Date(sinceText);
if (Number.isNaN(since.getTime())) {
  throw new Error(`Invalid --since value: ${sinceText}`);
}
const until = untilText ? new Date(untilText) : null;
if (until && Number.isNaN(until.getTime())) {
  throw new Error(`Invalid --until value: ${untilText}`);
}
if (until && until <= since) {
  throw new Error("--until must be later than --since");
}

const rosterJson = JSON.parse(fs.readFileSync(rosterPath, "utf8"));

function normalizeRosterItem(item) {
  const student = item?.student && typeof item.student === "object" ? item.student : item;
  if (!student || typeof student !== "object") return null;
  const userId = String(student.userId || student.user_id || student.id || "").trim();
  if (!userId) return null;
  return {
    ...student,
    userId,
    childName: student.childName || student.studentName || student.name || "",
    parentName: student.parentName || "",
    wechatNickName: student.wechatNickName || "",
    workWechatMatchInfoOutbound: student.workWechatMatchInfoOutbound || {},
  };
}

function uniqueRoster(items) {
  const seen = new Set();
  const rows = [];
  for (const item of items || []) {
    const normalized = normalizeRosterItem(item);
    if (!normalized || seen.has(normalized.userId)) continue;
    seen.add(normalized.userId);
    rows.push(normalized);
  }
  return rows;
}

const roster = uniqueRoster(
  rosterJson?.data?.items
  || rosterJson?.items
  || rosterJson?.students
  || rosterJson?.rows
  || [],
);
if (!Array.isArray(roster) || !roster.length) {
  throw new Error(`No roster rows found in ${rosterPath}`);
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${url}`);
  }
  return response.json();
}

const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
const page = targets.find(
  (target) =>
    target.type === "page" &&
    target.webSocketDebuggerUrl &&
    target.url.includes("codecamp-crm.codemao.cn") &&
    !target.url.includes("not_login"),
);

if (!page) {
  throw new Error(`No logged-in CRM page found on Chrome port ${port}`);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let sequence = 0;

function send(method, params = {}) {
  const id = ++sequence;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      if (!pending.has(id)) return;
      pending.delete(id);
      reject(new Error(`Timeout waiting for ${method}`));
    }, 120000);
    pending.set(id, { resolve, reject, timeout });
  });
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject, timeout } = pending.get(message.id);
  pending.delete(message.id);
  clearTimeout(timeout);
  message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
});

const expression = `
(async () => {
  const classCode = ${JSON.stringify(normalizedClassCode)};
  const groupKeyword = ${JSON.stringify(groupKeyword)};
  const sinceMs = ${JSON.stringify(since.getTime())};
  const untilMs = ${JSON.stringify(until?.getTime() ?? null)};
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
      throw new Error(JSON.stringify({ status: response.status, url, body, response: json }).slice(0, 3000));
    }
    return json;
  }

  const groupSearch = await post(
    "https://codecamp-marketing.codemao.cn/chat/session/search/list",
    {
      businessCode,
      phone: "",
      userId: "",
      chatId: "",
      page: 1,
      limit: 100
    }
  );
  const groups = (groupSearch?.data?.items || []).filter(
    (group) =>
      String(group.chatName || "").includes(classCode) &&
      String(group.chatName || "").replaceAll(" ", "").includes(groupKeyword.replaceAll(" ", ""))
  );

  const results = [];
  for (const group of groups) {
    const response = await post(
      "https://codecamp-marketing.codemao.cn/chat/session/search",
      {
        chatId: group.chatId,
        corpId: group.corpId,
        chatOwnerDismiss: group.chatOwnerDismiss || 0,
        businessCode,
        searchContent: "",
        searchType: "0",
        msgId: "",
        limit: 500,
        direction: ""
      }
    );
    const messages = (response?.data || []).filter(
      (message) =>
        message.msgType === "solitaire" &&
        String(message.userWechatName || "").trim() &&
        Number(message.msgTime || 0) >= sinceMs &&
        (untilMs === null || Number(message.msgTime || 0) < untilMs)
    );
    results.push({ group, messages });
  }

  return JSON.stringify({
    fetchedAt: new Date().toISOString(),
    classCode,
    groupKeyword,
    sinceMs,
    untilMs,
    groups: results
  });
})()
`;

function normalizeUrl(value) {
  return String(value || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function safeWrite(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.tmp`;
  fs.writeFileSync(tempPath, content, "utf8");
  fs.renameSync(tempPath, filePath);
}

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

  const rawValue = result.result?.value;
  const fetched = typeof rawValue === "string" ? JSON.parse(rawValue) : rawValue;
  const byAlias = new Map();
  const aliasEntries = [];
  const byAvatar = new Map();

  function normalizeAlias(value) {
    return String(value || "")
      .normalize("NFKC")
      .trim()
      .toLowerCase()
      .replace(/[\s\u200b\u200c\u200d]+/g, "")
      .replace(/同学|同學|学生|學生|小朋友|宝贝|寶貝|宝宝|寶寶|孩子|本人/g, "")
      .replace(/爸爸|妈妈|爸|妈|家长|家長|老师|老師/g, "")
      .replace(/的?ipad|iphone|手机|平板/g, "")
      .replace(/[\p{P}\p{S}]/gu, "");
  }

  function aliasVariants(value) {
    const base = normalizeAlias(value);
    if (!base) return [];
    const variants = new Set([base]);
    const parts = String(value || "")
      .normalize("NFKC")
      .split(/[-_—/|｜,，、;；:：\s()[\]{}【】]+/);
    for (const part of parts) {
      const normalized = normalizeAlias(part);
      if (normalized && normalized.length >= 2) variants.add(normalized);
    }
    return [...variants];
  }

  function addAlias(student, value, method, allowContains = false) {
    for (const alias of aliasVariants(value)) {
      if (!byAlias.has(alias)) byAlias.set(alias, []);
      byAlias.get(alias).push({ student, method });
      if (allowContains && alias.length >= 2) {
        aliasEntries.push({ alias, student, method });
      }
    }
  }

  function uniqueCandidates(items) {
    const seen = new Set();
    const result = [];
    for (const item of items || []) {
      const id = String(item.student?.userId || "");
      if (!id || seen.has(id)) continue;
      seen.add(id);
      result.push(item);
    }
    return result;
  }

  function matchBySenderName(senderName) {
    const normalized = normalizeAlias(senderName);
    if (!normalized) return { candidates: [], method: "" };
    const exact = uniqueCandidates(byAlias.get(normalized) || []);
    if (exact.length) {
      return { candidates: exact.map((item) => item.student), method: exact.length === 1 ? exact[0].method : "多候选精确匹配" };
    }
    const fuzzy = uniqueCandidates(
      aliasEntries.filter((item) => normalized.includes(item.alias) || item.alias.includes(normalized)),
    );
    return {
      candidates: fuzzy.map((item) => item.student),
      method: fuzzy.length === 1 ? `${fuzzy[0].method}模糊匹配` : "多候选模糊匹配",
    };
  }

  for (const student of roster) {
    const wechat = student.workWechatMatchInfoOutbound || {};
    const avatar = normalizeUrl(wechat.headImg);
    addAlias(student, wechat.nickName, "企微昵称");
    addAlias(student, student.wechatNickName, "缓存企微昵称");
    addAlias(student, student.childName, "学生姓名", true);
    addAlias(student, student.parentName, "家长姓名");
    addAlias(student, wechat.remarkName, "企微备注", true);
    addAlias(student, wechat.parentName, "企微家长姓名");
    addAlias(student, wechat.childName, "企微孩子姓名", true);
    if (avatar) {
      if (!byAvatar.has(avatar)) byAvatar.set(avatar, []);
      byAvatar.get(avatar).push(student);
    }
  }

  const rawMessages = fetched.groups.flatMap(({ group, messages }) =>
    messages.map((message) => ({ group, message })),
  );
  const latestByGroupAndSender = new Map();
  for (const item of rawMessages) {
    const key = `${item.group.chatId}\u0000${item.message.userWechatName || ""}\u0000${normalizeUrl(item.message.userHeadUrl)}`;
    const current = latestByGroupAndSender.get(key);
    if (!current || Number(item.message.msgTime || 0) > Number(current.message.msgTime || 0)) {
      latestByGroupAndSender.set(key, item);
    }
  }

  const rows = [];
  for (const { group, message } of latestByGroupAndSender.values()) {
    const nickname = String(message.userWechatName || "").trim();
    const avatar = normalizeUrl(message.userHeadUrl);
    const nameMatch = matchBySenderName(nickname);
    let candidates = nameMatch.candidates;
    let matchMethod = nameMatch.method;
    if (candidates.length !== 1 && avatar) {
      const avatarCandidates = byAvatar.get(avatar) || [];
      if (avatarCandidates.length === 1) {
        candidates = avatarCandidates;
        matchMethod = "????";
      } else if (!candidates.length) {
        candidates = avatarCandidates;
        matchMethod = avatarCandidates.length > 1 ? "???????" : matchMethod;
      }
    }
    const student = candidates.length === 1 ? candidates[0] : null;
    rows.push({
      groupName: group.chatName || "",
      chatId: group.chatId || "",
      msgId: message.msgId || "",
      msgTime: Number(message.msgTime || 0),
      wechatName: nickname,
      userId: student?.userId || "",
      studentName: student?.childName || "",
      matchStatus: student ? "已匹配" : candidates.length > 1 ? "多候选" : "待核",
      matchMethod: student ? matchMethod : "",
      candidateIds: candidates.map((item) => item.userId).join("|"),
      candidateNames: candidates.map((item) => item.childName).join("|"),
    });
  }

  rows.sort((left, right) =>
    left.groupName.localeCompare(right.groupName, "zh-CN") ||
    right.msgTime - left.msgTime ||
    left.wechatName.localeCompare(right.wechatName, "zh-CN"),
  );

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const jsonPath = path.join(outDir, "latest.json");
  const snapshotPath = path.join(outDir, `solitaire-${stamp}.json`);
  const csvPath = path.join(outDir, "latest.csv");
  const output = {
    ...fetched,
    studentCount: roster.length,
    rawSolitaireMessageCount: rawMessages.length,
    uniqueSolitaireSenderCount: rows.length,
    matchedCount: rows.filter((row) => row.matchStatus === "已匹配").length,
    reviewCount: rows.filter((row) => row.matchStatus !== "已匹配").length,
    rows,
  };

  safeWrite(jsonPath, `${JSON.stringify(output, null, 2)}\n`);
  safeWrite(snapshotPath, `${JSON.stringify(output, null, 2)}\n`);
  const headers = [
    "群名",
    "学生ID",
    "学生姓名",
    "企微昵称",
    "接龙时间",
    "匹配状态",
    "匹配方式",
    "候选学生ID",
    "候选学生姓名",
    "chatId",
    "msgId",
  ];
  const csvRows = rows.map((row) => [
    row.groupName,
    row.userId,
    row.studentName,
    row.wechatName,
    new Date(row.msgTime).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }),
    row.matchStatus,
    row.matchMethod,
    row.candidateIds,
    row.candidateNames,
    row.chatId,
    row.msgId,
  ]);
  safeWrite(csvPath, `\ufeff${[headers, ...csvRows].map((row) => row.map(csvCell).join(",")).join("\n")}\n`);

  console.log(JSON.stringify({
    groups: fetched.groups.map(({ group, messages }) => ({
      chatId: group.chatId,
      chatName: group.chatName,
      solitaireMessages: messages.length,
    })),
    rawSolitaireMessageCount: output.rawSolitaireMessageCount,
    uniqueSolitaireSenderCount: output.uniqueSolitaireSenderCount,
    matchedCount: output.matchedCount,
    reviewCount: output.reviewCount,
    latestJson: jsonPath,
    latestCsv: csvPath,
  }, null, 2));
} finally {
  ws.close();
}
