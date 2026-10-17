import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type Route } from "playwright/test";

// AINA Protocol 2.0 canvas tools (design §9.5) with the whiteboard reference AINA's real canvas: the host sends a
// canvas call over the viewer channel, the shell opens the closed canvas, runs the tool in it and posts the result.
const AINA = "com.example.whiteboard";
const CONVERSATION = "conv_board";
const NOW = "2026-10-17T04:00:00.000Z";
const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../examples/whiteboard-aina/package");
const MANIFEST = JSON.parse(readFileSync(path.join(PACKAGE, "aina.json"), "utf-8"));
const UI_HEADERS = {
  "Content-Security-Policy":
    "sandbox allow-scripts allow-forms allow-downloads; default-src 'self'; script-src 'self' 'unsafe-inline'; " +
    "style-src 'self' 'unsafe-inline'; connect-src 'none'; frame-ancestors http://127.0.0.1:5173",
  "Access-Control-Allow-Origin": "*",
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installApi(page: Page, call: Record<string, unknown>) {
  const state = { acks: [] as string[], results: [] as Record<string, unknown>[], contexts: [] as string[], heartbeats: [] as Record<string, unknown>[], streams: 0 };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const apiPath = url.pathname.replace(/^\/api/, "");
    const method = request.method();
    if (method === "GET" && apiPath === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "GET" && apiPath.startsWith(`/aina-ui/${AINA}/1.0.0/`)) {
      const file = apiPath.slice(`/aina-ui/${AINA}/1.0.0/`.length);
      const type = file.endsWith(".html") ? "text/html" : file.endsWith(".css") ? "text/css" : "text/javascript";
      return route.fulfill({ status: 200, contentType: `${type}; charset=utf-8`, headers: UI_HEADERS, body: readFileSync(path.join(PACKAGE, file)) });
    }
    if (method === "GET" && apiPath === `/ainas/${AINA}`) {
      return json(route, {
        manifest: {
          aina: { id: AINA, name: "Whiteboard", version: "1.0.0" },
          runtime: {
            type: "package",
            package: MANIFEST,
            tools: [{ name: "load_board", description: "Load", effect: "read", visibility: ["model", "app"], status: "active" }],
          },
        },
      });
    }
    if (method === "GET" && apiPath === "/installations") return json(route, [{ aina_id: AINA, configuration: {}, granted_permissions: ["storage"], status: "active" }]);
    if (method === "POST" && apiPath === `/ainas/${AINA}/app-tools/load_board`) return json(route, { structuredContent: { scene: [] } });
    if (method === "POST" && apiPath === `/ainas/${AINA}/open`) {
      return json(route, {
        aina_id: AINA, name: "Whiteboard", description: "", version: "1.0.0", conversation_id: CONVERSATION,
        route: `/chat/${CONVERSATION}?aina=${AINA}`,
        main_widget: {
          id: `${AINA}-canvas`, kind: "aina_canvas", title: "Whiteboard", description: "", fields: [], actions: [], apps: [],
          canvas: { kind: "package", version: "1.0.0", entry: "canvas/index.html", url: null },
        },
      });
    }
    if (method === "GET" && apiPath === `/conversations/${CONVERSATION}`) {
      return json(route, {
        id: CONVERSATION, user_id: "anonymous", tenant_id: "default", workspace_id: null, title: "Board", category: "general",
        status: "active", run_status: "running", config: {}, enabled_ainas: [], active_aina_ids: [], messages: [
          { id: "m1", role: "user", content: "Circle the login box", content_type: "text", widgets: [], created_at: NOW },
        ], created_at: NOW, updated_at: NOW,
      });
    }
    if (method === "GET" && apiPath === `/conversations/${CONVERSATION}/canvas-viewer`) {
      // One canvas call, then no more: EventSource stops reconnecting on 204.
      state.streams += 1;
      if (state.streams > 1) return route.fulfill({ status: 204, body: "" });
      return route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
        body: `: connected\n\nevent: canvas.tool.requested\ndata: ${JSON.stringify(call)}\n\n`,
      });
    }
    if (method === "POST" && apiPath === `/conversations/${CONVERSATION}/canvas-viewer/heartbeat`) {
      state.heartbeats.push(request.postDataJSON());
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "POST" && apiPath.endsWith("/ack")) {
      state.acks.push(apiPath.split("/").at(-2) ?? "");
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "POST" && apiPath.startsWith(`/conversations/${CONVERSATION}/canvas-calls/`)) {
      state.results.push({ call: apiPath.split("/").pop(), ...request.postDataJSON() });
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "PUT" && apiPath === `/conversations/${CONVERSATION}/canvas-context/${AINA}`) {
      state.contexts.push(String(request.postDataJSON().value));
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "GET" && apiPath.startsWith("/approvals")) return json(route, []);
    return json(route, { error: { message: `Unhandled ${method} ${apiPath}` } }, 404);
  });
  return state;
}

test("FE-E2E-AINA-05 a canvas call opens the closed canvas, runs there and returns its result", async ({ page }) => {
  const state = await installApi(page, {
    type: "canvas.tool.requested",
    call_id: "call_circle",
    aina_id: AINA,
    name: "draw_circle",
    arguments: { x: 550, y: 360, radius: 120, color: "#e03131" },
    timeout_ms: 10_000,
  });
  await page.goto(`/chat/${CONVERSATION}`);

  const frame = page.frameLocator("iframe[sandbox]");
  await expect(frame.locator("#shapes ellipse")).toHaveCount(1);
  await expect(frame.locator("#shapes ellipse")).toHaveAttribute("stroke", "#e03131");
  await expect.poll(() => state.results.length).toBe(1);
  const [posted] = state.results;
  expect(posted.call).toBe("call_circle");
  expect(state.acks).toEqual(["call_circle"]); // receipt is confirmed before the canvas runs the tool
  expect(posted.client_id).toMatch(/^tab_[0-9a-f]{32}$/);
  expect(posted.result).toMatchObject({ structuredContent: { element_id: expect.stringMatching(/^el_/) } });
  // The canvas told the agent what it now shows; heartbeats say this tab is the one being looked at.
  await expect.poll(() => state.contexts.at(-1) ?? "").toContain("1 shape(s) on the 1200x800 whiteboard");
  await expect.poll(() => state.heartbeats.length, { timeout: 8000 }).toBeGreaterThan(0);
  expect(state.heartbeats[0]).toMatchObject({ client_id: posted.client_id, visible: true });
});

test("FE-E2E-AINA-06 a canvas call for a tool the canvas does not implement fails back to the agent", async ({ page }) => {
  const state = await installApi(page, {
    type: "canvas.tool.requested",
    call_id: "call_rotate",
    aina_id: AINA,
    name: "rotate_scene",
    arguments: {},
    timeout_ms: 3_000,
  });
  await page.goto(`/chat/${CONVERSATION}`);

  await expect.poll(() => state.results.length, { timeout: 8000 }).toBe(1);
  expect(state.results[0]).toMatchObject({ call: "call_rotate", error: { message: expect.stringContaining("rotate_scene") } });
});
