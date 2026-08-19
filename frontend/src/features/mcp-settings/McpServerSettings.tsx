import { FormEvent, useCallback, useEffect, useState } from "react";
import { Cable, ChevronDown, ChevronRight, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";

type AuthenticationType = "none" | "bearer" | "api_key" | "oauth2";

interface McpToolDescriptor {
  name: string;
  title: string | null;
  description: string;
}

interface McpServerRecord {
  server_id: string;
  name: string;
  endpoint: string;
  protocol_version: string | null;
  server_name: string | null;
  server_version: string | null;
  status: "published" | "disabled";
  side_effect_level: "none" | "low" | "high";
  tools: McpToolDescriptor[];
}

const initialForm = {
  server_id: "",
  name: "",
  endpoint: "",
  authentication_type: "none" as AuthenticationType,
  header_name: "Authorization",
  credential: "",
  side_effect_level: "low" as "none" | "low" | "high",
};

export function McpServerSettings() {
  const [servers, setServers] = useState<McpServerRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [form, setForm] = useState(initialForm);

  const load = useCallback(async () => {
    try {
      setServers(await api.get<McpServerRecord[]>("/mcp-servers"));
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy("create");
    setError(null);
    try {
      const authentication = form.authentication_type === "none"
        ? { type: "none" }
        : {
            type: form.authentication_type,
            header_name: form.header_name,
            credential: form.credential,
          };
      const created = await api.post<McpServerRecord>("/mcp-servers", {
        server_id: form.server_id,
        name: form.name,
        endpoint: form.endpoint,
        authentication,
        side_effect_level: form.side_effect_level,
      });
      setServers((current) => [...current, created]);
      setExpanded(created.server_id);
      setForm(initialForm);
      setShowEditor(false);
    } catch (submitError) {
      setError(apiErrorMessage(submitError));
    } finally {
      setBusy(null);
    }
  }

  async function refresh(serverId: string) {
    setBusy(serverId);
    setError(null);
    try {
      const refreshed = await api.post<McpServerRecord>(`/mcp-servers/${serverId}/refresh`, {});
      setServers((current) => current.map((item) => item.server_id === serverId ? refreshed : item));
    } catch (refreshError) {
      setError(apiErrorMessage(refreshError));
    } finally {
      setBusy(null);
    }
  }

  async function remove(serverId: string) {
    setBusy(serverId);
    setError(null);
    try {
      await api.delete(`/mcp-servers/${serverId}`);
      setServers((current) => current.filter((item) => item.server_id !== serverId));
    } catch (removeError) {
      setError(apiErrorMessage(removeError));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mt-8 border-t border-line pt-5" aria-label="MCP Server 设置">
      <div className="mb-3 flex items-end gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Cable className="h-4 w-4 text-accent" />
            <h2 className="text-[14px] font-extrabold text-ink">MCP Server</h2>
          </div>
          <p className="mt-1 text-[11.5px] text-ink-muted">连接标准 MCP 服务，自动发现工具并提供给 Agent 使用。</p>
        </div>
        <div className="flex-1" />
        <span className="text-[11px] font-semibold text-ink-subtle">{servers.length} 个</span>
        <button type="button" onClick={() => setShowEditor(true)} className="btn-primary h-8 px-3 text-[11.5px]">
          <Plus className="h-3.5 w-3.5" />连接 Server
        </button>
      </div>

      {error ? <div className="mb-3 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</div> : null}

      {showEditor ? (
        <form onSubmit={(event) => void submit(event)} className="mb-3 rounded-lg border border-accent/30 bg-white p-4 shadow-card">
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-[13px] font-extrabold text-ink">连接 Streamable HTTP Server</h3>
            <span className="flex-1" />
            <button type="button" onClick={() => setShowEditor(false)} className="btn-ghost h-7 w-7 p-0" aria-label="关闭"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Server ID"><input required pattern="[A-Za-z0-9._-]+" value={form.server_id} onChange={(event) => setForm({ ...form, server_id: event.target.value })} className="input h-9" placeholder="company-tools" /></Field>
            <Field label="名称"><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className="input h-9" placeholder="Company Tools" /></Field>
            <div className="md:col-span-2"><Field label="MCP 地址"><input required type="url" value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} className="input h-9 font-mono" placeholder="https://mcp.example.com/mcp" /></Field></div>
            <Field label="认证方式">
              <select value={form.authentication_type} onChange={(event) => setForm({ ...form, authentication_type: event.target.value as AuthenticationType })} className="input h-9">
                <option value="none">无需认证</option><option value="bearer">Bearer Token</option><option value="api_key">API Key</option><option value="oauth2">OAuth2 Token</option>
              </select>
            </Field>
            <Field label="副作用级别">
              <select value={form.side_effect_level} onChange={(event) => setForm({ ...form, side_effect_level: event.target.value as typeof form.side_effect_level })} className="input h-9">
                <option value="none">无副作用</option><option value="low">低风险</option><option value="high">高风险（执行前确认）</option>
              </select>
            </Field>
            {form.authentication_type !== "none" ? (
              <>
                <Field label="Header"><input required value={form.header_name} onChange={(event) => setForm({ ...form, header_name: event.target.value })} className="input h-9 font-mono" /></Field>
                <Field label="凭据"><input required type="password" value={form.credential} onChange={(event) => setForm({ ...form, credential: event.target.value })} className="input h-9" autoComplete="new-password" /></Field>
              </>
            ) : null}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" onClick={() => setShowEditor(false)} className="btn-outline h-8 px-3 text-[11.5px]">取消</button>
            <button type="submit" disabled={busy === "create"} className="btn-primary h-8 px-3 text-[11.5px] disabled:opacity-50">
              <RefreshCw className={classNames("h-3.5 w-3.5", busy === "create" && "animate-spin")} />连接并发现工具
            </button>
          </div>
        </form>
      ) : null}

      {loading ? <div className="h-24 animate-pulse rounded-lg border border-line bg-white" /> : servers.length ? (
        <div className="space-y-2">
          {servers.map((server) => {
            const open = expanded === server.server_id;
            return (
              <article key={server.server_id} className="overflow-hidden rounded-lg border border-line bg-white shadow-card">
                <button type="button" onClick={() => setExpanded(open ? null : server.server_id)} className="flex w-full items-center gap-3 px-4 py-3 text-left">
                  {open ? <ChevronDown className="h-4 w-4 text-accent" /> : <ChevronRight className="h-4 w-4 text-ink-subtle" />}
                  <div className="flex h-8 w-8 items-center justify-center rounded bg-accent-soft text-accent"><Cable className="h-4 w-4" /></div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2"><h3 className="text-[13px] font-extrabold text-ink">{server.name}</h3><span className="rounded bg-success-soft px-1.5 py-0.5 text-[9.5px] font-bold text-success-deep">已连接</span></div>
                    <p className="mt-0.5 truncate font-mono text-[10.5px] text-ink-subtle">{server.endpoint}</p>
                  </div>
                  <div className="flex-1" />
                  <span className="text-[10.5px] font-semibold text-ink-muted">{server.tools.length} 个工具</span>
                  <button type="button" onClick={(event) => { event.stopPropagation(); void refresh(server.server_id); }} disabled={busy === server.server_id} className="btn-ghost h-8 w-8 p-0" title="重新发现"><RefreshCw className={classNames("h-3.5 w-3.5", busy === server.server_id && "animate-spin")} /></button>
                  <button type="button" onClick={(event) => { event.stopPropagation(); void remove(server.server_id); }} disabled={busy === server.server_id} className="btn-ghost h-8 w-8 p-0 text-danger" title="删除"><Trash2 className="h-3.5 w-3.5" /></button>
                </button>
                {open ? <div className="border-t border-line px-4 py-3"><p className="mb-2 text-[10.5px] text-ink-muted">协议 {server.protocol_version ?? "自动协商"} · 服务版本 {server.server_version ?? "未知"}</p><div className="grid gap-2 md:grid-cols-2">{server.tools.map((tool) => <div key={tool.name} className="rounded-md border border-line bg-app-soft px-3 py-2"><p className="font-mono text-[11px] font-bold text-ink">{tool.title ?? tool.name}</p><p className="mt-1 line-clamp-2 text-[10.5px] leading-4 text-ink-muted">{tool.description}</p></div>)}</div></div> : null}
              </article>
            );
          })}
        </div>
      ) : <div className="rounded-lg border border-dashed border-line-strong bg-white px-6 py-8 text-center text-[11.5px] text-ink-muted">尚未连接 MCP Server。连接后，发现的工具会自动出现在 Agent 能力列表中。</div>}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-[10.5px] font-bold text-ink-muted">{label}</span>{children}</label>;
}
