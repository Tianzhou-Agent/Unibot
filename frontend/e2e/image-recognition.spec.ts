import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type Route } from "playwright/test";

// Image Recognition is an explicitly installed example AINA 2.0 package: its built canvas, served with the host's sandbox
// headers, calls the plugin's app tools over the UI bridge. The API is mocked.
const AINA = "unibot.image-recognition";
const VERSION = "2.0.0";
const PACKAGE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../examples/image-recognition-aina/package",
);
const UI_HEADERS = {
  "Content-Security-Policy":
    "sandbox allow-scripts allow-forms allow-downloads; default-src 'self'; script-src 'self' 'unsafe-inline'; " +
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'none'; form-action 'none'; " +
    "base-uri 'none'; frame-ancestors http://127.0.0.1:5173",
  "X-Content-Type-Options": "nosniff",
  "Access-Control-Allow-Origin": "*",
};
const TOOLS = [
  { name: "detect", description: "Detect objects.", effect: "read", visibility: ["app"], status: "active" },
  { name: "vision_status", description: "Service status.", effect: "read", visibility: ["app"], status: "active" },
];
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4J8AAAAASUVORK5CYII=";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installApi(page: Page, { failDetect = false } = {}) {
  const detections: Record<string, unknown>[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const apiPath = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (method === "GET" && apiPath === "/auth/config") {
      return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    }
    if (method === "GET" && apiPath.startsWith(`/aina-ui/${AINA}/${VERSION}/`)) {
      const file = apiPath.slice(`/aina-ui/${AINA}/${VERSION}/`.length);
      const type = file.endsWith(".html") ? "text/html" : file.endsWith(".css") ? "text/css" : "text/javascript";
      return route.fulfill({ status: 200, contentType: `${type}; charset=utf-8`, headers: UI_HEADERS, body: readFileSync(path.join(PACKAGE, file)) });
    }
    if (method === "POST" && apiPath === `/ainas/${AINA}/open`) {
      return json(route, {
        aina_id: AINA,
        name: "Image Recognition",
        description: "Detect objects with YOLO26m.",
        version: VERSION,
        conversation_id: null,
        route: `/chat?aina=${AINA}`,
        main_widget: {
          id: `${AINA}-canvas`,
          kind: "aina_canvas",
          title: "Image Recognition",
          description: "Detect objects with YOLO26m.",
          fields: [],
          actions: [],
          apps: [],
          canvas: { kind: "package", version: VERSION, entry: "canvas/index.html", url: null },
        },
      });
    }
    if (method === "GET" && apiPath === `/ainas/${AINA}`) {
      return json(route, { manifest: { aina: { id: AINA, name: "Image Recognition", version: VERSION }, runtime: { type: "package", bundled: false, tools: TOOLS } } });
    }
    if (method === "GET" && apiPath === "/installations") return json(route, [{ aina_id: AINA, status: "active", installed_version: VERSION, granted_permissions: [] }]);
    if (method === "POST" && apiPath === `/ainas/${AINA}/app-tools/vision_status`) {
      return json(route, {
        structuredContent: { status: "ready", model: "yolo26m", device: "cuda:0", requested_device: "auto", gpu_name: "NVIDIA GeForce RTX 3080 Ti" },
      });
    }
    if (method === "POST" && apiPath === `/ainas/${AINA}/app-tools/detect`) {
      const { arguments: args } = request.postDataJSON() as { arguments: Record<string, unknown> };
      detections.push(args);
      if (failDetect) {
        return json(route, { error: { code: "TOOL_ERROR", message: "Tool 'detect' reported an error: UNAVAILABLE: the vision service is unavailable" } }, 502);
      }
      return json(route, {
        structuredContent: {
          model: "yolo26m",
          device: "cuda:0",
          image: { width: 32, height: 24 },
          detections: [{ class_id: 0, label: "person", label_zh: "人", confidence: 0.95, box: { x1: 2, y1: 3, x2: 22, y2: 23 } }],
          summary: { 人: 1 },
          inference_ms: 18.4,
        },
      });
    }
    if (method === "GET" && apiPath.startsWith("/approvals")) return json(route, []);
    return json(route, { error: { message: `Unhandled ${method} ${apiPath}` } }, 404);
  });
  return detections;
}

test("FE-E2E-010 选择或粘贴图片后展示 YOLO 目标框和结构化结果", async ({ page }) => {
  const detections = await installApi(page);
  await page.goto(`/chat?aina=${AINA}`);
  const frame = page.frameLocator("iframe[sandbox]");

  await expect(frame.getByRole("heading", { name: "YOLO26m 目标检测" })).toBeVisible();
  await expect(frame.getByText("yolo26m · NVIDIA GeForce RTX 3080 Ti", { exact: true })).toBeVisible();

  await frame.getByLabel("选择识别图片").setInputFiles({
    name: "sample.png",
    mimeType: "image/png",
    buffer: Buffer.from(PNG_BASE64, "base64"),
  });

  await expect(frame.getByTestId("detection-box-0")).toBeVisible();
  await expect(frame.getByText("人", { exact: true })).toBeVisible();
  await expect(frame.getByText("person", { exact: true })).toBeVisible();
  await expect(frame.getByText("95.0%", { exact: true })).toBeVisible();
  await expect(frame.getByText("GPU", { exact: true })).toBeVisible();
  // Read tools run without asking; the image travels as base64 through the bridge.
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  expect(detections).toEqual([{ image_base64: PNG_BASE64, content_type: "image/png", filename: "sample.png" }]);

  await frame.getByRole("button", { name: "清除图片" }).click();
  const canvas = page.frames().find((item) => item.url().includes("/aina-ui/"))!;
  await canvas.evaluate((base64) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], "sample.png", { type: "image/png" }));
    window.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, PNG_BASE64);

  await expect(frame.getByTestId("detection-box-0")).toBeVisible();
  expect(detections).toHaveLength(2);
});

test("FE-E2E-011 识别服务的错误以用户语言显示", async ({ page }) => {
  await installApi(page, { failDetect: true });
  await page.goto(`/chat?aina=${AINA}`);
  const frame = page.frameLocator("iframe[sandbox]");

  await frame.getByLabel("选择识别图片").setInputFiles({ name: "sample.png", mimeType: "image/png", buffer: Buffer.from(PNG_BASE64, "base64") });

  await expect(frame.getByText("图片识别服务暂时不可用。")).toBeVisible();
});
