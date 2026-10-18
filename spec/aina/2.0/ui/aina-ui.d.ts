// Types of @unibot/aina-ui, the UI bridge for AINA Protocol 2.0 canvases and widgets (design §9.4).

/** What the shell tells the frame on `getContext()`. */
export interface AinaContext {
  protocol: "aina-ui/2.0";
  /** The user's language, e.g. "en" or "zh". */
  locale: string;
  /** `--aina-*` CSS custom properties; also applied to the document. */
  theme: Record<string, string>;
  /** The installation's non-secret settings (`config.schema` in aina.json). */
  config: Record<string, unknown>;
  mode: "canvas" | "widget";
  aina: { id: string; version: string };
  /** Widgets only. */
  purpose?: "confirmation" | "result" | "display";
  /** Widgets only: the tool call the widget shows. */
  tool?: string;
  arguments?: Record<string, unknown>;
  /** Result and display widgets only. */
  result?: ToolResult | null;
}

/** A tool result as Unibot hands it to the UI: the structured content and the text of the content blocks. */
export interface ToolResult<T = unknown> {
  structuredContent?: T;
  content?: string;
}

/** What a canvas tool handler returns: an MCP result (structured content up to 64 KiB, at most one image). */
export interface CanvasToolResult {
  structuredContent?: unknown;
  content?: ({ type: "text"; text: string } | { type: "image"; data: string; mimeType: string })[];
}

export interface Host {
  /** Locale, theme, non-secret configuration and, for widgets, the tool call it shows. */
  getContext(): Promise<AinaContext>;
  /** Call one of this AINA's app-visible tools; Unibot asks the user before calls that change data. */
  callTool<T = unknown>(name: string, args?: Record<string, unknown>): Promise<ToolResult<T>>;
  /** Send a message into the conversation as the user. */
  sendPrompt(text: string): Promise<void>;
  setTitle(text: string): Promise<void>;
  notify(text: string): Promise<void>;
  /** URL canvases only: a short-lived pairwise identity token for your own backend. */
  getIdentityToken(): Promise<string>;
  /** Widgets only: open this AINA's canvas beside the conversation. */
  openCanvas(): Promise<void>;
  /** Widgets only: request a height up to 480 px. */
  resize(height: number): Promise<{ height: number }>;
  /** Confirmation widgets only: propose edited arguments for the pending call. */
  proposeArguments(args: Record<string, unknown>): Promise<void>;
  /** Canvas only: implement a canvas tool declared in aina.json; return an MCP-shaped result. */
  registerTool(name: string, handler: (args: Record<string, unknown>) => CanvasToolResult | void | Promise<CanvasToolResult | void>): Promise<void>;
  /** Canvas only: a summary of what the canvas shows (at most 4000 characters), sent with the agent's requests. */
  setModelContext(value: string): Promise<void>;
  /** "tool_result": the agent changed data in chat; "context_changed": settings or locale changed. Returns an unsubscribe. */
  on(type: "tool_result" | "context_changed", listener: (data: unknown) => void): () => void;
}

export const host: Host;
