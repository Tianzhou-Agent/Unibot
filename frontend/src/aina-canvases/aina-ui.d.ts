// Types of the AINA UI SDK (spec/aina/2.0/ui/aina-ui.js), which first-party canvases import as "@aina-ui".
declare module "@aina-ui" {
  export interface AinaContext {
    protocol: string;
    locale: string;
    theme: Record<string, string>;
    config: Record<string, unknown>;
  }

  export interface ToolResult<T = unknown> {
    structuredContent?: T;
    content?: string;
  }

  export const host: {
    getContext(): Promise<AinaContext>;
    callTool<T = unknown>(name: string, args?: Record<string, unknown>): Promise<ToolResult<T>>;
    sendPrompt(text: string): Promise<void>;
    setTitle(text: string): Promise<void>;
    notify(text: string): Promise<void>;
    registerTool(name: string, handler: (args: Record<string, unknown>) => unknown): Promise<void>;
    setModelContext(value: string): Promise<void>;
    on(type: "tool_result" | "context_changed", listener: (data: unknown) => void): () => void;
  };
}
