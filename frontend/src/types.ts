export type SessionKind = "conversation" | "task" | "event" | "app";
export type SessionStatus = "active" | "running" | "pending" | "done" | "idle" | "archived";

export interface SessionSummary {
  id: string;
  title: string;
  kind: SessionKind;
  status: SessionStatus;
  appId?: string;
  preview: string;
  updatedAt: string;
  unread?: number;
  pinned?: boolean;
}

export type FilterTab = "all" | "task" | "app" | "event";

export interface FileChip {
  id: string;
  name: string;
  sizeBytes?: number;
  mimeType?: string;
}

export type MessageBlock =
  | { kind: "text"; text: string }
  | { kind: "result"; text: string }
  | { kind: "tool_call"; name: string; args?: Record<string, unknown>; result?: string };

export type SurfaceBlock =
  | { kind: "choices"; intro?: string; options: ChoiceOption[] }
  | { kind: "confirm"; title: string; description: string; confirmLabel?: string; cancelLabel?: string; tone?: "warning" | "info" }
  | { kind: "status"; text: string; tone: "info" | "success" }
  | { kind: "error"; text: string }
  | { kind: "loading"; text?: string }
  | { kind: "form"; title: string; hint?: string; fields: FormField[]; submitLabel?: string; cancelLabel?: string };

export interface ChoiceOption {
  id: string;
  title: string;
  description: string;
  icon: string;
  iconTone: "blue" | "green" | "indigo" | "slate";
  selected?: boolean;
}

export interface FormField {
  id: string;
  label: string;
  placeholder: string;
  required?: boolean;
  value?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  blocks?: MessageBlock[];
  surface?: SurfaceBlock;
  files?: FileChip[];
  createdAt: string;
  runState?: "thinking" | "running" | "done" | "error";
}

export interface ChatThread {
  sessionId: string;
  title: string;
  messages: ChatMessage[];
}

export interface AppDescriptor {
  id: string;
  name: string;
  description: string;
  icon: string;
  tone: "blue" | "green" | "indigo" | "amber" | "slate";
  category: "system" | "builtin" | "extension";
  enabled: boolean;
  highlight?: boolean;
  routesTo?: string;
}

export interface MemoryItem {
  id: string;
  title: string;
  meta: string;
  source: string;
  sourceTone: "accent" | "muted" | "warning";
  actions: Array<"keep" | "delete">;
  category: "fact" | "goal" | "preference" | "pending";
  scope: "workspace" | "global" | "session";
  confidence: number;
  updatedLabel: string;
  selected?: boolean;
}

export interface MemoryStats {
  total: number;
  fact: number;
  goal: number;
  preference: number;
  pending: number;
}

export interface ModelProvider {
  id: string;
  name: string;
  recommended?: boolean;
  baseUrl: string;
  model: string;
  apiKeyMasked: string;
  timeoutSec: number;
}

export interface EnvVar {
  key: string;
  value: string;
  scope: "global" | "workspace" | "session";
}

export interface ConnectionStatus {
  state: "connected" | "degraded" | "disconnected";
  testedAt: string;
  statusCode: number;
  latencyMs: number;
  note: string;
}

export interface SettingsResponse {
  providers: ModelProvider[];
  selectedProviderId: string;
  env: EnvVar[];
  connection: ConnectionStatus;
}
