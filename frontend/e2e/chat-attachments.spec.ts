import { expect, test, type Route } from "playwright/test";

// Files attached in the composer are uploaded to the conversation and the message names them by file_ref.
const CONVERSATION = "conv-files";
const NOW = "2026-10-19T04:00:00.000Z";
const REF = `file_${"a".repeat(32)}`;

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

test("FE-E2E-ATTACH-01 attached files are uploaded, sent by ref and shown on the message", async ({ page }) => {
  const messages: unknown[] = [];
  const uploads: string[] = [];
  let streamed: { message?: string; attachments?: string[] } | null = null;
  const attachment = { file_ref: REF, name: "receipt.txt", content_type: "text/plain", size_bytes: 11 };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "GET" && path === `/conversations/${CONVERSATION}`) {
      return json(route, {
        id: CONVERSATION, user_id: "anonymous", tenant_id: "default", workspace_id: null, title: "Receipts", category: "general",
        status: "active", run_status: "idle", config: {}, enabled_ainas: [], active_aina_ids: [], messages,
        created_at: NOW, updated_at: NOW,
      });
    }
    if (method === "POST" && path === `/conversations/${CONVERSATION}/attachments`) {
      uploads.push(request.postDataBuffer()?.toString("utf8").includes("receipt.txt") ? "receipt.txt" : "other");
      return json(route, attachment, 201);
    }
    if (method === "POST" && path === "/chat/stream") {
      streamed = request.postDataJSON();
      messages.push(
        { id: "m1", role: "user", content: streamed?.message, content_type: "text", widgets: [], attachments: [attachment], created_at: NOW },
        { id: "m2", role: "assistant", content: "A taxi receipt.", content_type: "text", widgets: [], created_at: NOW },
      );
      const response = {
        conversation_id: CONVERSATION, message_id: "m2", content: "A taxi receipt.", status: "completed", trace_id: "t1",
        iterations: 1, usage: { input_tokens: 1, output_tokens: 1 }, approval: null, widgets: [], opened_app_id: null,
      };
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message.completed\ndata: ${JSON.stringify({ type: "message.completed", response })}\n\n`,
      });
    }
    if (method === "GET" && path.endsWith("/aina-tasks")) return json(route, []);
    if (method === "GET" && path.startsWith("/approvals")) return json(route, []);
    if (method === "GET" && path === "/notifications") return json(route, { items: [], unread: 0 });
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });

  await page.goto(`/chat/${CONVERSATION}`);
  await page.getByTestId("composer-files").setInputFiles([
    { name: "receipt.txt", mimeType: "text/plain", buffer: Buffer.from("Taxi 42 EUR") },
    { name: "draft.txt", mimeType: "text/plain", buffer: Buffer.from("not this one") },
  ]);
  await page.getByRole("button", { name: "移除 draft.txt" }).click();
  await expect(page.getByRole("button", { name: /^移除/ })).toHaveCount(1);
  await page.getByRole("textbox", { name: "消息", exact: true }).fill("What is this receipt?");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();

  const chip = page.locator("main").getByRole("link", { name: "receipt.txt" });
  await expect(chip).toHaveAttribute("href", `/api/conversations/${CONVERSATION}/attachments/${REF}`);
  await expect(page.getByRole("button", { name: /^移除/ })).toHaveCount(0);
  expect(uploads).toEqual(["receipt.txt"]);
  expect(streamed).toMatchObject({ message: "What is this receipt?", attachments: [REF] });
});
