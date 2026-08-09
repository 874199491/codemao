// Export CRM auth cookies from a Chrome instance started with --remote-debugging-port.
// Usage: node scripts/export_crm_cookies_from_chrome.mjs --port 9222 --out crm_cookies.json

import { writeFileSync } from "node:fs";

function arg(name, fallback) {
  const idx = process.argv.indexOf(name);
  return idx >= 0 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const port = arg("--port", "9222");
const out = arg("--out", "crm_cookies.json");
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page =
  targets.find((target) => target.type === "page" && /codecamp-crm\.codemao\.cn/.test(target.url)) ||
  targets.find((target) => target.type === "page");

if (!page?.webSocketDebuggerUrl) {
  throw new Error(`No debuggable Chrome page found on port ${port}`);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
let nextId = 1;
const pending = new Map();

function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

ws.addEventListener("message", (event) => {
  const data = JSON.parse(event.data);
  if (data.id && pending.has(data.id)) {
    const { resolve, reject } = pending.get(data.id);
    pending.delete(data.id);
    data.error ? reject(new Error(JSON.stringify(data.error))) : resolve(data.result);
  }
});

await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));
const result = await send("Network.getCookies", {
  urls: [
    "https://codecamp-crm.codemao.cn/",
    "https://lbk-crm-teacher-web-api.codemao.cn/",
    "https://api-codecamp-crm.codemao.cn/",
  ],
});

const cookies = {};
for (const cookie of result.cookies) {
  if (cookie.name === "internal_account_token" || cookie.name === "admin-authorization") {
    cookies[cookie.name] = cookie.value;
  }
}

if (!cookies.internal_account_token || !cookies["admin-authorization"]) {
  throw new Error("CRM auth cookies were not found. Log in to CRM in the debug Chrome window first.");
}

writeFileSync(out, JSON.stringify(cookies, null, 2), "utf8");
console.log(
  JSON.stringify(
    {
      out,
      keys: Object.keys(cookies),
      lengths: Object.fromEntries(Object.entries(cookies).map(([key, value]) => [key, value.length])),
    },
    null,
    2,
  ),
);

ws.close();
