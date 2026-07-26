import type { ChatResponse } from "@/types";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `请求失败：${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });
  const text = await res.text();
  const body = text ? safeJson(text) : null;
  if (!res.ok) {
    throw new ApiError(res.status, body, `请求 ${path} 失败（${res.status}）`);
  }
  return body as T;
}

export interface BlobResponse {
  blob: Blob;
  filename?: string;
}

async function requestBlob(path: string, init: RequestInit = {}): Promise<BlobResponse> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text ? safeJson(text) : null, `请求 ${path} 失败（${res.status}）`);
  }
  return {
    blob: await res.blob(),
    filename: responseFilename(res.headers.get("Content-Disposition")),
  };
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const body = error.body as { error?: { user_message?: string; message?: string } } | null;
    return body?.error?.user_message ?? body?.error?.message ?? error.message;
  }
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

export type StreamEvent =
  | { type: "message.delta"; delta: string }
  | { type: "tool.requested" | "tool.completed"; kind: "tool" | "aina" | "builtin"; id: string }
  | { type: "routing.started"; candidate_count: number }
  | { type: "routing.completed"; kind: "aina" | "system"; id?: string | null }
  | { type: "approval.required"; approval_id: string; capabilities: string[] }
  | { type: "message.completed"; response: ChatResponse }
  | { type: "error"; code?: string; source?: string; error?: { message?: string; code?: string } };

export async function streamChat(
  payload: {
    message: string;
    conversation_id?: string;
    user_id?: string;
    tenant_id?: string;
    capability?: string;
    preferred_aina_id?: string;
    ui_context?: string;
  },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text ? safeJson(text) : null);
  }
  if (!response.body) throw new Error("浏览器未提供流式响应体。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (!data) continue;
      onEvent(JSON.parse(data) as StreamEvent);
    }
    if (done) break;
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function responseFilename(contentDisposition: string | null): string | undefined {
  if (!contentDisposition) return undefined;
  const utf8 = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (utf8) {
    try {
      return decodeURIComponent(utf8);
    } catch {
      return utf8;
    }
  }
  return contentDisposition.match(/filename="([^"]+)"/i)?.[1]
    ?? contentDisposition.match(/filename=([^;]+)/i)?.[1]?.trim();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  postForm: <T>(path: string, body: FormData) => request<T>(path, { method: "POST", body }),
  getBlob: (path: string) => requestBlob(path),
  postBlob: (path: string, body?: unknown) =>
    requestBlob(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
};
