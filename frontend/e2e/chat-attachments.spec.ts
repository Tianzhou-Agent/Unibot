import { expect, test, type Page, type Route } from "playwright/test";

// Files are uploaded when they are picked; the message names them by file_ref, and each sent file is a document.
const CONVERSATION = "conv-files";
const NOW = "2026-10-19T04:00:00.000Z";
const RECEIPT = `file_${"a".repeat(32)}`;
const DRAFT = `file_${"b".repeat(32)}`;
const BUDGET = `file_${"c".repeat(32)}`;

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function uploaded(name: string, file_ref: string) {
  return { file_ref, name, content_type: "text/plain", size_bytes: 11 };
}

/** A conversation backend whose upload handler is `onUpload` (file name -> response). */
async function backend(page: Page, onUpload: (name: string, route: Route) => Promise<void>) {
  const state = { messages: [] as unknown[], deleted: [] as string[], streamed: null as { message?: string; attachments?: string[] } | null };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "GET" && path === `/conversations/${CONVERSATION}`) {
      return json(route, {
        id: CONVERSATION, user_id: "anonymous", tenant_id: "default", workspace_id: null, title: "Receipts", category: "general",
        status: "active", run_status: "idle", config: {}, enabled_ainas: [], active_aina_ids: [], messages: state.messages,
        created_at: NOW, updated_at: NOW,
      });
    }
    if (method === "POST" && path === "/attachments") {
      const name = request.postDataBuffer()?.toString("utf8").match(/filename="([^"]+)"/)?.[1] ?? "";
      return onUpload(name, route);
    }
    if (method === "DELETE" && path.startsWith("/attachments/")) {
      state.deleted.push(path.slice("/attachments/".length));
      return route.fulfill({ status: 204 });
    }
    if (method === "POST" && path === "/chat/stream") {
      state.streamed = request.postDataJSON();
      state.messages.push(
        {
          id: "m1", role: "user", content: state.streamed?.message, content_type: "text", widgets: [], created_at: NOW,
          attachments: [{ ...uploaded("receipt.txt", RECEIPT), document: "Uploads/receipt.txt.md" }],
        },
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
  return state;
}

function text(name: string, content: string) {
  return { name, mimeType: "text/plain", buffer: Buffer.from(content) };
}

test("FE-E2E-ATTACH-01 picked files are uploaded at once, sent by ref and link to their documents", async ({ page }) => {
  const refs: Record<string, string> = { "receipt.txt": RECEIPT, "draft.txt": DRAFT };
  const state = await backend(page, (name, route) => json(route, uploaded(name, refs[name]), 201));

  await page.goto(`/chat/${CONVERSATION}`);
  await page.getByTestId("composer-files").setInputFiles([text("receipt.txt", "Taxi 42 EUR"), text("draft.txt", "not this one")]);
  const chips = page.locator("[data-status]");
  await expect(chips).toHaveCount(2);
  await expect(chips.and(page.locator('[data-status="ready"]'))).toHaveCount(2);
  await page.getByRole("button", { name: "移除 draft.txt" }).click();
  await expect(chips).toHaveCount(1);
  await expect.poll(() => state.deleted).toEqual([DRAFT]);
  await page.getByRole("textbox", { name: "消息", exact: true }).fill("What is this receipt?");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();

  const chip = page.locator("main").getByRole("link", { name: "receipt.txt" });
  await expect(chip).toHaveAttribute("href", `/chat/${CONVERSATION}?aina=unibot-documents&document=Uploads%2Freceipt.txt.md`);
  await expect(chips).toHaveCount(0);
  expect(state.streamed).toMatchObject({ message: "What is this receipt?", attachments: [RECEIPT] });
});

test("FE-E2E-ATTACH-02 unreadable files are refused and a failed upload blocks sending until it is retried", async ({ page }) => {
  let attempts = 0;
  await backend(page, (name, route) => {
    attempts += 1;
    return attempts === 1
      ? json(route, { error: { code: "UNREADABLE_FILE", message: "The file is not UTF-8 text" } }, 422)
      : json(route, uploaded(name, BUDGET), 201);
  });

  await page.goto(`/chat/${CONVERSATION}`);
  const picker = page.getByTestId("composer-files");
  await expect(picker).toHaveAttribute("accept", /\.pdf/);
  await picker.setInputFiles([{ name: "photo.png", mimeType: "image/png", buffer: Buffer.from("png") }]);
  await expect(page.getByText("无法读取 photo.png", { exact: false })).toBeVisible();
  await expect(page.locator("[data-status]")).toHaveCount(0);

  await picker.setInputFiles([text("budget.csv", "a,b")]);
  await expect(page.locator('[data-status="failed"]')).toHaveCount(1);
  await expect(page.getByText("budget.csv 上传失败", { exact: false })).toBeVisible();
  await page.getByRole("textbox", { name: "消息", exact: true }).fill("Summarize");
  const send = page.getByRole("button", { name: "发送消息", exact: true });
  await expect(send).toBeDisabled();

  await page.getByRole("button", { name: "重新上传 budget.csv" }).click();
  await expect(page.locator('[data-status="ready"]')).toHaveCount(1);
  await expect(send).toBeEnabled();
});
