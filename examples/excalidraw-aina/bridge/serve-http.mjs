// Serves a local Excalidraw MCP server (yctimlin/mcp_excalidraw, which itself speaks only stdio) over Streamable
// HTTP, so Unibot can use it as a remote AINA. It reuses the server's own tool table and the MCP SDK it ships with,
// and draws on the canvas service the same way the stdio server does.
//
//   EXCALIDRAW_DIR=/path/to/excalidraw node examples/excalidraw-aina/bridge/serve-http.mjs
//
// Environment: EXCALIDRAW_DIR (required, a built checkout), PORT (default 3001), EXPRESS_SERVER_URL (the canvas
// service, default http://127.0.0.1:3000). It listens on 127.0.0.1 only.
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

const dir = process.env.EXCALIDRAW_DIR;
if (!dir) {
  console.error("Set EXCALIDRAW_DIR to the Excalidraw MCP checkout (with dist/ built).");
  process.exit(1);
}
const port = Number(process.env.PORT ?? 3001);
// The server resolves relative export paths (exports/…) from its working folder, as the stdio server run from the
// checkout does.
process.chdir(dir);
// The server reads its configuration when its modules load, so set it first.
process.env.EXPRESS_SERVER_URL ??= "http://127.0.0.1:3000";
process.env.ENABLE_CANVAS_SYNC ??= "true";
process.env.EXCALIDRAW_NO_AUTOSTART ??= "1";
process.env.EXCALIDRAW_EXPORT_DIR ??= path.join(dir, "exports");
process.env.LOG_FILE_PATH ??= path.join(dir, "logs", "mcp-http.log");

const load = (relative) => import(pathToFileURL(path.join(dir, relative)).href);
const { createMcpHandler } = await load("node_modules/@modelcontextprotocol/server/dist/index.mjs");
const { excalidrawMcpServerFactory } = await load("dist/core/mcp-server.js");
const handler = createMcpHandler(excalidrawMcpServerFactory);

const server = http.createServer(async (incoming, outgoing) => {
  try {
    if (new URL(incoming.url ?? "/", "http://127.0.0.1").pathname !== "/mcp") {
      outgoing.writeHead(404).end();
      return;
    }
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
    const headers = new Headers();
    for (const [name, value] of Object.entries(incoming.headers)) {
      for (const item of [value].flat()) if (item !== undefined) headers.append(name, item);
    }
    const hasBody = !["GET", "HEAD"].includes(incoming.method ?? "GET");
    const response = await handler.fetch(
      new Request(`http://127.0.0.1:${port}${incoming.url}`, {
        method: incoming.method,
        headers,
        body: hasBody ? Buffer.concat(chunks) : undefined,
      }),
    );
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    if (response.body) for await (const chunk of response.body) outgoing.write(chunk);
    outgoing.end();
  } catch (error) {
    console.error("MCP request failed:", error);
    if (!outgoing.headersSent) outgoing.writeHead(500);
    outgoing.end();
  }
});
server.listen(port, "127.0.0.1", () => console.log(`Excalidraw MCP over HTTP at http://127.0.0.1:${port}/mcp`));
