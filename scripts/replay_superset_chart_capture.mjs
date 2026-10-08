import "./ws-shim.mjs";
import fs from "node:fs";

const port = Number(process.argv.find((arg) => arg.startsWith("--port="))?.split("=")[1] || 9223);
const capture = process.argv.find((arg) => arg.startsWith("--capture="))?.slice("--capture=".length);
const output = process.argv.find((arg) => arg.startsWith("--output="))?.slice("--output=".length);
const sliceId = Number(process.argv.find((arg) => arg.startsWith("--slice-id="))?.split("=")[1] || 3143);
const conditionCapture = process.argv.find((arg) => arg.startsWith("--condition-capture="))?.slice("--condition-capture=".length);
const conditionSliceId = Number(process.argv.find((arg) => arg.startsWith("--condition-slice-id="))?.split("=")[1] || 0);
const explicitTimeRange = process.argv.find((arg) => arg.startsWith("--time-range="))?.slice("--time-range=".length);

if (!capture || !output) throw new Error("Missing --capture or --output");

const records = fs.readFileSync(capture, "utf8")
  .split(/\r?\n/)
  .filter(Boolean)
  .map((line) => {
    try { return JSON.parse(line); } catch { return null; }
  })
  .filter(Boolean);
const needle = `slice_id%22%3A${sliceId}`;
const source = [...records].reverse().find((item) =>
  item.event === "body" && String(item.url || "").includes(needle) && item.postJson
);
if (!source) throw new Error(`Cannot find captured chart request for slice ${sliceId}`);

const requestPayload = structuredClone(source.postJson);
if (conditionCapture && conditionSliceId) {
  const conditionNeedle = `slice_id%22%3A${conditionSliceId}`;
  const conditionRecords = fs.readFileSync(conditionCapture, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try { return JSON.parse(line); } catch { return null; }
    })
    .filter(Boolean);
  const conditionSource = [...conditionRecords].reverse().find((item) =>
    item.event === "body" && String(item.url || "").includes(conditionNeedle) && item.postJson
  );
  if (!conditionSource) throw new Error(`Cannot find condition request for slice ${conditionSliceId}`);
  const sourceQuery = requestPayload.queries?.[0];
  const conditionQuery = conditionSource.postJson.queries?.[0];
  if (!sourceQuery || !conditionQuery) throw new Error("Captured request has no query payload");
  sourceQuery.time_range = conditionQuery.time_range;
  sourceQuery.filters = structuredClone(conditionQuery.filters || []);
  requestPayload.form_data.extra_form_data = structuredClone(
    conditionSource.postJson.form_data?.extra_form_data || {}
  );
  requestPayload.form_data.adhoc_filters = structuredClone(
    conditionSource.postJson.form_data?.adhoc_filters || []
  );
}
if (explicitTimeRange) {
  requestPayload.queries[0].time_range = explicitTimeRange;
  requestPayload.form_data.extra_form_data = {
    ...(requestPayload.form_data.extra_form_data || {}),
    time_range: explicitTimeRange,
  };
}

const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
const page = targets.find((item) =>
  item.type === "page" && item.webSocketDebuggerUrl && String(item.url || "").includes("bigdata-superset.codemao.cn")
);
if (!page) throw new Error("No logged-in Superset tab found");

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`Timeout waiting for ${method}`));
      }
    }, 240000);
    pending.set(id, { resolve, reject, timer });
  });
}
ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject, timer } = pending.get(message.id);
  pending.delete(message.id);
  clearTimeout(timer);
  message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
});

await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});
await send("Runtime.enable");
const expression = `(async () => {
  const csrfResponse = await fetch('/api/v1/security/csrf_token/', {credentials: 'include'});
  const csrfJson = await csrfResponse.json();
  const response = await fetch(${JSON.stringify(source.url)}, {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfJson.result},
    body: ${JSON.stringify(JSON.stringify(requestPayload))}
  });
  return {status: response.status, text: await response.text()};
})()`;
const evaluated = await send("Runtime.evaluate", {
  expression,
  awaitPromise: true,
  returnByValue: true,
});
ws.close();
const value = evaluated.result?.value;
if (!value || typeof value.text !== "string") {
  throw new Error(`Unexpected Superset response: ${JSON.stringify(evaluated).slice(0, 1000)}`);
}
if (value.status !== 200) throw new Error(`Superset HTTP ${value.status}: ${value.text.slice(0, 1000)}`);
const payload = JSON.parse(value.text);
fs.writeFileSync(output, JSON.stringify(payload, null, 2), "utf8");
const first = Array.isArray(payload.result) ? payload.result[0] || {} : {};
console.log(JSON.stringify({
  output,
  status: value.status,
  rowcount: first.rowcount,
  rows: Array.isArray(first.data) ? first.data.length : 0,
  columns: Array.isArray(first.colnames) ? first.colnames.length : 0,
  timeRange: requestPayload.queries?.[0]?.time_range,
}, null, 2));
