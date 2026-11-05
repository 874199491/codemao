#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

const workspace = "C:\\Users\\PC\\Documents\\codemao";
const outputPath = path.join(workspace, "CRM_RAW_CHAT_TRANSCRIPT.md");

const sources = [
  {
    name: "主任务：CRM 教学服务自动化",
    threadId: "019f12f0-3199-7d52-8f0d-4e3c71f7b47d",
    file: "C:\\Users\\PC\\.codex\\sessions\\2026\\06\\29\\rollout-2026-06-29T18-32-46-019f12f0-3199-7d52-8f0d-4e3c71f7b47d.jsonl",
    startAt: "2026-06-29T10:47:54.892Z",
  },
  {
    name: "独立任务：打开 CRM 新页面并监听",
    threadId: "019f27c4-cae8-7950-a4e4-de5382ed7198",
    file: "C:\\Users\\PC\\.codex\\sessions\\2026\\07\\03\\rollout-2026-07-03T19-37-21-019f27c4-cae8-7950-a4e4-de5382ed7198.jsonl",
  },
  {
    name: "独立任务：打开 CRM",
    threadId: "019f54e0-4bdf-7c13-8dd8-22aae19c7d83",
    file: "C:\\Users\\PC\\.codex\\sessions\\2026\\07\\12\\rollout-2026-07-12T13-50-13-019f54e0-4bdf-7c13-8dd8-22aae19c7d83.jsonl",
  },
];

function formatShanghaiTime(timestamp) {
  const date = new Date(timestamp);
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function roleLabel(message) {
  if (message.kind === "user_message") return "用户";
  if (message.phase === "commentary") return "助手（过程更新）";
  if (message.phase === "final_answer") return "助手（最终回复）";
  return "助手";
}

async function readVisibleMessages(source) {
  const messages = [];
  const stream = fs.createReadStream(source.file, { encoding: "utf8" });
  const lines = readline.createInterface({ input: stream, crlfDelay: Infinity });

  for await (const line of lines) {
    if (!line.includes('"type":"user_message"') && !line.includes('"type":"agent_message"')) {
      continue;
    }

    let item;
    try {
      item = JSON.parse(line);
    } catch {
      continue;
    }

    if (item.type !== "event_msg") continue;
    if (!item.payload || !["user_message", "agent_message"].includes(item.payload.type)) continue;
    if (source.startAt && item.timestamp < source.startAt) continue;

    const text = String(item.payload.message ?? "").replace(/\r\n/g, "\n").trimEnd();
    if (!text) continue;

    messages.push({
      timestamp: item.timestamp,
      kind: item.payload.type,
      phase: item.payload.phase ?? null,
      text,
    });
  }

  return messages;
}

const sections = [];
let totalMessages = 0;
let totalUserMessages = 0;
let totalAssistantMessages = 0;

for (const source of sources) {
  if (!fs.existsSync(source.file)) {
    throw new Error(`Session log not found: ${source.file}`);
  }

  const messages = await readVisibleMessages(source);
  totalMessages += messages.length;
  totalUserMessages += messages.filter((message) => message.kind === "user_message").length;
  totalAssistantMessages += messages.filter((message) => message.kind === "agent_message").length;

  const body = messages
    .map((message) => {
      const heading = `### ${formatShanghaiTime(message.timestamp)} | ${roleLabel(message)}`;
      return `${heading}\n\n${message.text}`;
    })
    .join("\n\n---\n\n");

  sections.push(
    [
      `## ${source.name}`,
      "",
      `任务 ID：\`${source.threadId}\``,
      "",
      `消息数量：${messages.length}`,
      "",
      body,
    ].join("\n"),
  );
}

const generatedAt = formatShanghaiTime(new Date().toISOString());
const header = [
  "# CodeMao CRM 原始聊天记录",
  "",
  `导出时间：${generatedAt}（Asia/Shanghai）`,
  "",
  `总消息数：${totalMessages}（用户 ${totalUserMessages}，助手 ${totalAssistantMessages}）`,
  "",
  "> 本文件来自本机 Codex 会话日志，保留用户和助手在界面中可见的消息原文。仅排除了系统/开发者指令、隐藏推理、工具调用和工具输出。主任务从用户正式提出 CodeMao CRM 教学服务需求的位置开始导出，之前的费用报销测试没有包含。",
  "",
  "> 隐私警告：原始对话可能包含学生 ID、班级 ID、内部钉钉链接、本机路径和业务操作记录，只适合在获得授权的内部人员之间分享。",
  "",
  "",
].join("\n");

fs.writeFileSync(outputPath, `${header}${sections.join("\n\n---\n\n")}\n`, "utf8");

console.log(
  JSON.stringify(
    {
      outputPath,
      totalMessages,
      totalUserMessages,
      totalAssistantMessages,
      sources: sources.length,
    },
    null,
    2,
  ),
);
