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

export type ConversationStatus = "active" | "archived" | "deleted";

export interface BackendMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  content_type: string;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }> | null;
  tool_call_id?: string | null;
  name?: string | null;
  trace_id?: string | null;
  created_at: string;
}

export interface ConversationRecord {
  id: string;
  user_id: string;
  tenant_id: string;
  title: string;
  status: ConversationStatus;
  config: Record<string, unknown>;
  enabled_ainas: string[];
  messages: BackendMessage[];
  created_at: string;
  updated_at: string;
}

export interface AuthenticationDefinition {
  type: "none" | "bearer" | "api_key" | "oauth2";
  header_name: string;
}

export interface ToolRecord {
  tool_id: string;
  name: string;
  description: string;
  version: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  endpoint: string;
  authentication: AuthenticationDefinition;
  timeout_seconds: number;
  retries: number;
  side_effect_level: "none" | "low" | "high";
  permissions: string[];
  visibility: "public" | "private" | "tenant";
  status: "testing" | "published" | "disabled";
  created_at: string;
}

export interface SkillRecord {
  skill_id: string;
  name: string;
  description: string;
  version: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  instructions: string;
  tools: string[];
  permissions: string[];
  publisher: string;
  visibility: "public" | "private" | "tenant";
  status: "draft" | "testing" | "published" | "deprecated" | "disabled" | "archived";
  created_at: string;
}

export interface AinaCapabilityDefinition {
  id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface AinaManifest {
  protocol_version: string;
  aina: {
    id: string;
    name: string;
    version: string;
    description: string;
    publisher: { id: string; name: string };
  };
  runtime: {
    type: "remote";
    endpoint: string;
    streaming: boolean;
    async_tasks: boolean;
  };
  capabilities: {
    skills: AinaCapabilityDefinition[];
    tools: AinaCapabilityDefinition[];
    ui: Array<Record<string, unknown>>;
    events: Array<Record<string, unknown>>;
  };
  permissions: string[];
  authentication: AuthenticationDefinition;
  health_check?: string | null;
}

export interface AinaRecord {
  manifest: AinaManifest;
  status: "registered" | "disabled";
  registered_at: string;
  last_health: Record<string, unknown>;
}

export interface AinaInstallation {
  aina_id: string;
  installed_version: string;
  user_id: string;
  tenant_id: string;
  granted_permissions: string[];
  configuration: Record<string, unknown>;
  status: "active" | "disabled";
  installed_at: string;
}

export interface ApprovalRecord {
  id: string;
  conversation_id: string;
  user_id: string;
  tenant_id: string;
  trace_id: string;
  tool_calls: Array<{
    id: string;
    type: string;
    function: { name: string; arguments: string };
  }>;
  capability_names: string[];
  status: "pending" | "approved" | "denied" | "executed";
  created_at: string;
  resolved_at?: string | null;
}

export interface ChatResponse {
  conversation_id: string;
  message_id?: string | null;
  content: string;
  status: "completed" | "approval_required" | "failed";
  trace_id: string;
  iterations: number;
  usage: { input_tokens: number; output_tokens: number };
  approval?: ApprovalRecord | null;
}

export interface TraceEvent {
  timestamp: string;
  kind: string;
  status: string;
  target_type?: string | null;
  target_id?: string | null;
  duration_ms?: number | null;
  details: Record<string, unknown>;
}

export interface TraceRecord {
  trace_id: string;
  conversation_id?: string | null;
  user_id: string;
  tenant_id: string;
  status: "running" | "completed" | "approval_required" | "failed";
  events: TraceEvent[];
  created_at: string;
  completed_at?: string | null;
}

export interface AdminSummary {
  conversations: number;
  tools: number;
  skills: number;
  ainas: number;
  installations: number;
  traces: number;
}

export interface CapabilityOption {
  value: string;
  label: string;
  kind: "tool" | "aina";
}
