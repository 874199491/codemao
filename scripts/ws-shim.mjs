// WebSocket 兼容层：为没有全局 WebSocket 的 Node（21 以下）补齐。
//
// Node 21+ 自带全局 WebSocket，本模块会直接跳过。
// Node 18/20 会依次尝试 undici、ws 两个来源；都不可用时给出可操作的提示。
//
// 需要在工作目录执行 `npm i ws`（或在全局安装）后使用，或直接把 Node 升级到 21+。
if (typeof globalThis.WebSocket === "undefined") {
  let WS = null;
  for (const spec of ["undici", "ws"]) {
    try {
      const mod = await import(spec);
      WS = mod.WebSocket || mod.default || null;
      if (WS) break;
    } catch {
      // 换下一个来源继续尝试
    }
  }
  if (!WS) {
    throw new Error(
      `当前 Node（${process.version}）没有内置 WebSocket（需 Node 21 及以上）。` +
        "请升级 Node 到 21+，或在本工作目录执行 `npm i ws` 后重试。",
    );
  }
  globalThis.WebSocket = WS;
}
