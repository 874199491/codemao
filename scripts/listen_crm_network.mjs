import "./ws-shim.mjs";
import fs from "node:fs";
import path from "node:path";

const port = Number(process.argv.find((arg) => arg.startsWith("--port="))?.split("=")[1] || 9222);
const out = process.argv.find((arg) => arg.startsWith("--out="))?.split("=")[1] || "data/crm-network-capture.jsonl";
const urlPattern = process.argv.find((arg) => arg.startsWith("--pattern="))?.split("=")[1] || "codemao|crm|live|lesson|attendance|attend|student";

const matcher = new RegExp(urlPattern, "i");
fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
  return res.json();
}

const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
const pages = targets.filter((target) => target.type === "page" && target.webSocketDebuggerUrl);
const crmPage =
  pages.find((target) => /crm|codemao|lbk/i.test(`${target.url} ${target.title}`)) ||
  pages[0];

if (!crmPage) {
  throw new Error("No Chrome page target found. Open the CRM tab and try again.");
}

const ws = new WebSocket(crmPage.webSocketDebuggerUrl);
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
    }, 10000);
  });
}

function write(record) {
  fs.appendFileSync(out, `${JSON.stringify(record, null, 0)}\n`, "utf8");
}

function tryParseJson(text) {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

ws.addEventListener("open", async () => {
  await send("Network.enable");
  await send("Page.enable");
  console.log(`Listening on: ${crmPage.title}`);
  console.log(`URL: ${crmPage.url}`);
  console.log(`Writing: ${out}`);
  console.log(`Pattern: ${matcher}`);
  console.log("Now operate CRM. Press Ctrl+C when the demo is finished.");
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
    requests.set(requestId, {
      requestId,
      timestamp,
      type,
      url: request.url,
      method: request.method,
      requestHeaders: request.headers,
      postData: request.postData,
      postJson: tryParseJson(request.postData),
      initiatorType: initiator?.type,
    });
    write({ event: "request", ...requests.get(requestId) });
    return;
  }

  if (message.method === "Network.responseReceived") {
    const { requestId, response, type, timestamp } = message.params;
    if (!requests.has(requestId) && !matcher.test(response.url)) return;
    const prior = requests.get(requestId) || { requestId, url: response.url };
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
      const record = {
        event: "body",
        ...prior,
        responseBody: bodyJson ?? bodyText.slice(0, 50000),
        responseBodyTruncated: !bodyJson && bodyText.length > 50000,
      };
      write(record);
      console.log(`${prior.method || ""} ${prior.status || ""} ${prior.url}`);
    } catch (error) {
      write({ event: "body_error", requestId, url: prior.url, error: String(error.message || error) });
    }
  }
});

ws.addEventListener("error", (event) => {
  console.error("WebSocket error", event);
});
