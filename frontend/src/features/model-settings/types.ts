export type ProviderType = "openai" | "deepseek" | "openrouter" | "ollama" | "custom";

export interface ModelDefinition {
  id: string;
  name: string;
  model: string;
  enabled: boolean;
  is_default: boolean;
  context_window_tokens: number;
}

export interface ModelProvider {
  id: string;
  provider_type: ProviderType;
  name: string;
  base_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  timeout_seconds: number;
  models: ModelDefinition[];
}

export interface ActiveModel {
  source: "user" | "environment" | "unconfigured";
  provider_id?: string | null;
  provider_name?: string | null;
  model_id?: string | null;
  model_name?: string | null;
  model?: string | null;
}

export interface ModelSettingsResponse {
  providers: ModelProvider[];
  active_model: ActiveModel;
}

export interface ModelHealthResult {
  status: "healthy" | "unhealthy";
  checked_at: string;
  latency_ms: number;
  error?: string | null;
}

export interface ModelDiscoveryResponse {
  models: Array<{
    id: string;
    name: string;
    context_window_tokens?: number | null;
  }>;
}

export interface ModelProviderPayload {
  user_id: string;
  tenant_id: string;
  provider_type: ProviderType;
  name: string;
  base_url: string;
  api_key?: string;
  timeout_seconds: number;
  models: Array<{
    id?: string;
    name: string;
    model: string;
    enabled: boolean;
    context_window_tokens: number;
  }>;
}
