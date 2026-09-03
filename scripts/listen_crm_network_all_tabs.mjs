import fs from "node:fs";
import path from "node:path";

// 兼容 Node 20：其没有全局 WebSocket（Node 21+ 才有）。从内置 undici 补齐。
if (typeof WebSocket === "undefined") {
  const undici = await import("undici");
  globalThis.WebSocket = undici.WebSocket;
}

const port = Number(process.argv.find((arg) => arg.startsWith("--port="))?.split("=")[1] || 9222);
const out = process.argv.find((arg) => arg.startsWith("--out="))?.split("=")[1] || "data/crm-all-tabs-capture.jsonl";
const urlPattern = process.argv.find((arg) => arg.startsWith("--pattern="))?.split("=")[1] || "codemao|crm|wechat|message|session|conversation|chat|external|call|record|follow";
const pagePattern = process.argv.find((arg) => arg.startsWith("--page-pattern="))?.split("=")[1] || "codemao|crm|wechat|session|call";
const matcher = new RegExp(urlPattern, "i");
const pageMatcher = new RegExp(pagePattern, "i");

fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
  return res.json();
}

function write(record) {
  fs.appendFileSync(out, `${JSON.stringify(record)}\n`, "utf8");
}

function tryParseJson(text) {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

async function listenPage(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const pending = new Map();
  const requests = new Map();
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
      }, 15000);
    });
  }

  ws.addEventListener("open", async () => {
    await send("Network.enable");
    await send("Page.enable");
    console.log(`Listening tab: ${page.title} ${page.url}`);
  });

  ws.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
      return;
    }

    if (message.method === "Network.requestWillBeSent") {
      const { requestId, request, timestamp, type, initiator } = message.params;
      if (!matcher.test(request.url)) return;
      const record = {
        tabTitle: page.title,
        tabUrl: page.url,
        requestId,
        timestamp,
        type,
        url: request.url,
        method: request.method,
        requestHeaders: request.headers,
        postData: request.postData,
        postJson: tryParseJson(request.postData),
        initiatorType: initiator?.type,
      };
      requests.set(requestId, record);
      write({ event: "request", ...record });
      return;
    }

    if (message.method === "Network.responseReceived") {
      const { requestId, response, type, timestamp } = message.params;
      if (!requests.has(requestId) && !matcher.test(response.url)) return;
      const prior = requests.get(requestId) || { requestId, url: response.url, tabTitle: page.title, tabUrl: page.url };
      requests.set(requestId, {
        ...prior,
        responseTimestamp: timestamp,
        responseType: type,
        status: response.status,
        statusText: response.statusText,
        mimeType: response.mimeType,
        responseHeaders: response.headers,
      });
      write({
        event: "response",
        tabTitle: page.title,
        tabUrl: page.url,
        requestId,
        url: response.url,
        status: response.status,
        statusText: response.statusText,
        mimeType: response.mimeType,
      });
      return;
    }

    if (message.method === "Network.loadingFinished") {
      const { requestId } = message.params;
      const prior = requests.get(requestId);
      if (!prior) return;
      try {
        const bodyResult = await send("Network.getResponseBody", { requestId });
        const bodyText = bodyResult.base64Encoded
          ? Buffer.from(bodyResult.body, "base64").toString("utf8")
          : bodyResult.body;
        const bodyJson = tryParseJson(bodyText);
        write({
          event: "body",
          ...prior,
          responseBody: bodyJson ?? bodyText.slice(0, 100000),
          responseBodyTruncated: !bodyJson && bodyText.length > 100000,
        });
        console.log(`${prior.method || ""} ${prior.status || ""} ${prior.url}`);
      } catch (error) {
        write({ event: "body_error", tabTitle: page.title, tabUrl: page.url, requestId, url: prior.url, error: String(error.message || error) });
      }
    }
  });
}

const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
const pages = targets.filter((target) => {
  if (target.type !== "page" || !target.webSocketDebuggerUrl) return false;
  return pageMatcher.test(`${target.url} ${target.title}`);
});

if (!pages.length) {
  throw new Error("No matching Chrome page target found. Open the CRM tab and try again.");
}

console.log(`Writing: ${out}`);
console.log(`Request pattern: ${matcher}`);
console.log(`Page pattern: ${pageMatcher}`);
console.log(`Tabs: ${pages.length}`);
for (const page of pages) {
  await listenPage(page);
}
console.log("Now operate CRM. Close this command when the demo is finished.");
