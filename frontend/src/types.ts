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
  widgets: WidgetDefinition[];
  trace_id?: string | null;
  created_at: string;
}

export interface ConversationRecord {
  id: string;
  user_id: string;
  tenant_id: string;
  title: string;
  category: string;
  status: ConversationStatus;
  run_status: "idle" | "running" | "approval_required" | "failed";
  active_trace_id?: string | null;
  run_error?: string | null;
  run_started_at?: string | null;
  config: Record<string, unknown>;
  enabled_ainas: string[];
  active_aina_ids: string[];
  primary_aina_id?: string | null;
  last_aina_id?: string | null;
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
  instructions?: string | null;
}

export interface AinaUiCapabilityDefinition {
  id: string;
  kind: WidgetDefinition["kind"];
  description: string;
  instructions?: string | null;
}

export interface WidgetFieldDefinition {
  id: string;
  label: string;
  input_type: "text" | "number" | "textarea";
  placeholder: string;
  required: boolean;
  value?: string | null;
}

export interface WidgetActionDefinition {
  id: string;
  label: string;
  kind: "open_aina" | "prompt";
  aina_id?: string | null;
  prompt?: string | null;
  style: "primary" | "secondary";
}

export interface WidgetAppDefinition {
  aina_id: string;
  name: string;
  description: string;
  version: string;
  publisher: string;
  installed: boolean;
  has_main_widget: boolean;
}

export interface WidgetDocumentSectionDefinition {
  index: number;
  heading: string;
  level: number;
  occurrence: number;
  line_start: number;
  line_end: number;
}

export interface WidgetDefinition {
  id: string;
  kind: "app_list" | "form" | "markdown" | "panel" | "navigation" | "memory" | "document" | "document_outline";
  title: string;
  description: string;
  markdown?: string | null;
  fields: WidgetFieldDefinition[];
  actions: WidgetActionDefinition[];
  apps: WidgetAppDefinition[];
  document_name?: string | null;
  sections?: WidgetDocumentSectionDefinition[];
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
  runtime:
    | {
        type: "remote";
        endpoint: string;
        streaming: boolean;
        async_tasks: boolean;
      }
    | { type: "builtin" };
  capabilities: {
    skills: AinaCapabilityDefinition[];
    tools: AinaCapabilityDefinition[];
    ui: AinaUiCapabilityDefinition[];
    events: Array<Record<string, unknown>>;
  };
  main_widget?: WidgetDefinition | null;
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

export type ScheduleType = "interval" | "cron";
export type ScheduledAinaStatus = "never" | "running" | "succeeded" | "failed";

export interface ScheduledAinaTask {
  id: string;
  aina_id: string;
  user_id: string;
  tenant_id: string;
  name: string;
  schedule_type: ScheduleType;
  interval_seconds: number;
  cron_expression?: string | null;
  timezone: string;
  prompt?: string | null;
  input: Record<string, unknown>;
  enabled: boolean;
  next_run_at: string;
  last_run_at?: string | null;
  last_status: ScheduledAinaStatus;
  last_node_id?: string | null;
  last_error?: string | null;
  last_result?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduledAinaExecution {
  id: string;
  task_id: string;
  aina_id: string;
  user_id: string;
  tenant_id: string;
  trigger: "scheduled" | "manual";
  scheduled_for?: string | null;
  call_id: string;
  node_id: string;
  input: Record<string, unknown>;
  status: "running" | "succeeded" | "failed";
  result?: Record<string, unknown> | null;
  error?: string | null;
  started_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
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
  widgets: WidgetDefinition[];
}

export interface AinaCanvasResponse {
  aina_id: string;
  name: string;
  description: string;
  version: string;
  conversation_id?: string | null;
  route: string;
  main_widget: WidgetDefinition;
}

export interface DocumentSummary {
  name: string;
  size_bytes: number;
  modified_at?: string | null;
}

export interface DocumentRecord extends DocumentSummary {
  content: string;
}

export interface DocumentFolder {
  path: string;
  name: string;
}

export interface DocumentTreeResponse {
  folders: DocumentFolder[];
  documents: DocumentSummary[];
}

export interface DocumentSectionRecord {
  name: string;
  heading: string;
  level: number;
  occurrence: number;
  revision: string;
  content: string;
}

export interface DocumentSectionUpdateResult {
  name: string;
  previous_heading: string;
  heading: string;
  level: number;
  occurrence: number;
  revision: string;
  size_bytes: number;
  modified_at?: string | null;
}

export interface DocumentHeading {
  index: number;
  heading: string;
  level: number;
  occurrence: number;
  line_start: number;
  line_end: number;
}

export interface DocumentOutline {
  name: string;
  size_bytes: number;
  revision: string;
  headings: DocumentHeading[];
}

export type DocumentEditTaskStatus =
  | "queued"
  | "running"
  | "reviewing"
  | "merging"
  | "merged"
  | "completed"
  | "abandoned"
  | "conflict"
  | "failed";

export type DocumentDraftAiStatus = "queued" | "running" | "ready" | "failed";
export type DocumentDraftReviewStatus = "pending" | "merged" | "abandoned";

export interface DocumentDraftSection {
  id: string;
  heading: string;
  occurrence: number;
  level: number;
  base_content: string;
  draft_content: string;
  draft_revision: number;
  ai_status: DocumentDraftAiStatus;
  ai_instruction?: string | null;
  ai_base_revision: number;
  ai_error?: string | null;
  updated_by: "source" | "ai" | "user";
  review_status: DocumentDraftReviewStatus;
  resolved_at?: string | null;
}

export interface DocumentEditTask {
  id: string;
  document_name: string;
  title: string;
  description: string;
  status: DocumentEditTaskStatus;
  base_revision: string;
  user_id: string;
  tenant_id: string;
  sections: DocumentDraftSection[];
  version: number;
  error?: string | null;
  created_at: string;
  updated_at: string;
  merged_at?: string | null;
  abandoned_at?: string | null;
}

export interface DocumentEditTaskListResponse {
  items: DocumentEditTask[];
  total: number;
}

export interface DocumentTaskContext {
  documentName: string;
  taskId: string;
  taskTitle: string;
  taskStatus: DocumentEditTaskStatus;
  sectionId: string;
  sectionHeading: string;
  draftRevision: number;
}

export interface DocumentListResponse {
  items: DocumentSummary[];
  total: number;
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
  memories: number;
}

export type MemoryCategory = "fact" | "preference" | "goal" | "instruction";

export interface MemoryRecord {
  id: string;
  content: string;
  category: MemoryCategory;
  user_id: string;
  tenant_id: string;
  source_conversation_id?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  items: MemoryRecord[];
  total: number;
}

export interface MemoryStatsResponse {
  total: number;
  fact: number;
  preference: number;
  goal: number;
  instruction: number;
}

export interface CapabilityOption {
  value: string;
  label: string;
  kind: "tool" | "aina" | "builtin";
}
