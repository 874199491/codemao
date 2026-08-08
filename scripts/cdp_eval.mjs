const port = Number(process.argv.find((arg) => arg.startsWith("--port="))?.split("=")[1] || 9222);
const match = process.argv.find((arg) => arg.startsWith("--match="))?.split("=")[1] || "";
const expression = process.argv.find((arg) => arg.startsWith("--expr="))?.slice("--expr=".length);

if (!expression) {
  throw new Error("Missing --expr=...");
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
  return res.json();
}

const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
const page = targets.find((target) => {
  if (target.type !== "page" || !target.webSocketDebuggerUrl) return false;
  return match ? target.url.includes(match) || target.title.includes(match) : true;
});

if (!page) throw new Error(`No matching page for ${match}`);

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
    }, 10000);
  });
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
});

ws.addEventListener("open", async () => {
  try {
    await send("Runtime.enable");
    const result = await send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    console.log(JSON.stringify(result.result?.value ?? result, null, 2));
  } finally {
    ws.close();
  }
});
