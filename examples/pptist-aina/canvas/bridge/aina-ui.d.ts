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
  /**
   * The canvas runs in Superbot's headless canvas host while the user is away (design §9.5): nobody sees it, and the
   * AINA's write tools run without asking. Save the agent's changes before its tool answers, or they are lost.
   */
  background: boolean;
  aina: { id: string; version: string };
  /**
   * Canvas only: whether the canvas is open beside a conversation. Its tool calls are made in that conversation, so
   * the MCP server gets its (pairwise) `conversation_id`. `context_changed` tells the canvas when the user moves it to
   * another conversation, or a new conversation starts.
   */
  conversation?: boolean;
  /** Widgets only. */
  purpose?: "confirmation" | "result" | "display";
  /** Widgets only: the tool call the widget shows. */
  tool?: string;
  arguments?: Record<string, unknown>;
  /** Result and display widgets only. */
  result?: ToolResult | null;
}

/** A tool result as Superbot hands it to the UI: the structured content and the text of the content blocks. */
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
  /** Call one of this AINA's app-visible tools; Superbot asks the user before destructive calls. */
  callTool<T = unknown>(name: string, args?: Record<string, unknown>): Promise<ToolResult<T>>;
  /** Send a message into the conversation as the user. */
  sendPrompt(text: string): Promise<void>;
  setTitle(text: string): Promise<void>;
  notify(text: string): Promise<void>;
  /**
   * Save a file the app made to the user's Files (the workspace's, beside a workspace conversation), and download it
   * when the user is there. The shell asks the user first, once per opened frame. Files keeps only types Superbot can
   * read: text, Markdown, code, PDF, DOCX, XLSX and PPTX, of at most 20 MiB. Resolves to the file's name in Files, e.g.
   * "Uploads/Deck.pptx", and whether the browser downloaded it.
   */
  saveFile(name: string, data: Blob): Promise<{ name: string; downloaded: boolean }>;
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
  /**
   * "tool_result": the agent changed data in chat; "context_changed": settings, locale or, for a canvas, the
   * conversation changed (data `{ conversation: boolean }`). Returns an unsubscribe.
   */
  on(type: "tool_result" | "context_changed", listener: (data: unknown) => void): () => void;
}

export const host: Host;
