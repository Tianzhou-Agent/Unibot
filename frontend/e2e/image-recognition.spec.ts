import { expect, test, type Page, type Route } from "playwright/test";

const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4J8AAAAASUVORK5CYII=";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installVisionApi(page: Page) {
  let detectionRequests = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");

    if (request.method() === "GET" && path === "/auth/config") {
      return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    }

    if (request.method() === "POST" && path === "/ainas/unibot-image-recognition/open") {
      return json(route, {
        aina_id: "unibot-image-recognition",
        name: "图片识别",
        description: "使用 YOLO26m 检测图片中的目标。",
        version: "1.0.0",
        conversation_id: null,
        route: "/canvas/unibot-image-recognition",
        main_widget: {
          id: "unibot-image-recognition-main",
          kind: "panel",
          title: "图片识别",
          description: "粘贴或选择一张图片。",
          markdown: null,
          fields: [],
          actions: [],
          apps: [],
        },
      });
    }
    if (request.method() === "GET" && path === "/vision/health") {
      return json(route, {
        status: "ready",
        model: "yolo26m",
        device: "cuda:0",
        requested_device: "auto",
        gpu_name: "NVIDIA GeForce RTX 3080 Ti",
      });
    }
    if (request.method() === "POST" && path === "/vision/detect") {
      detectionRequests += 1;
      expect(request.headers()["content-type"]).toContain("multipart/form-data");
      const body = request.postDataBuffer()?.toString("latin1");
      expect(body).toContain('name="image"');
      expect(body).toContain("sample.png");
      return json(route, {
        model: "yolo26m",
        device: "cuda:0",
        image: { width: 32, height: 24 },
        detections: [
          {
            class_id: 0,
            label: "person",
            label_zh: "人",
            confidence: 0.95,
            box: { x1: 2, y1: 3, x2: 22, y2: 23 },
          },
        ],
        summary: { 人: 1 },
        inference_ms: 18.4,
      });
    }
    if (request.method() === "GET" && path.startsWith("/approvals")) return json(route, []);
    return json(route, { error: { message: `Unhandled ${request.method()} ${path}` } }, 404);
  });
  return { detectionRequests: () => detectionRequests };
}

test("FE-E2E-010 选择或粘贴图片后展示 YOLO 目标框和结构化结果", async ({ page }) => {
  const apiState = await installVisionApi(page);
  await page.goto("/canvas/unibot-image-recognition");

  await expect(page.getByRole("heading", { name: "YOLO26m 目标检测" })).toBeVisible();
  await expect(page.getByText("yolo26m · NVIDIA GeForce RTX 3080 Ti", { exact: true })).toBeVisible();

  await page.getByLabel("选择识别图片").setInputFiles({
    name: "sample.png",
    mimeType: "image/png",
    buffer: Buffer.from(PNG_BASE64, "base64"),
  });

  await expect(page.getByTestId("detection-box-0")).toBeVisible();
  await expect(page.getByText("人", { exact: true })).toBeVisible();
  await expect(page.getByText("person", { exact: true })).toBeVisible();
  await expect(page.getByText("95.0%", { exact: true })).toBeVisible();
  await expect(page.getByText("GPU", { exact: true })).toBeVisible();
  expect(apiState.detectionRequests()).toBe(1);

  await page.getByRole("button", { name: "清除图片" }).click();
  await page.evaluate((base64) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], "sample.png", { type: "image/png" }));
    window.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, PNG_BASE64);

  await expect(page.getByTestId("detection-box-0")).toBeVisible();
  expect(apiState.detectionRequests()).toBe(2);
});
