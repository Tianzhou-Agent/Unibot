import { useEffect, useState } from "react";
import { ChevronDown, RefreshCw, CircleCheck, Eye, EyeOff } from "lucide-react";
import { api } from "@/lib/api";
import { SETTINGS } from "@/mocks/seed";
import type { ConnectionStatus, EnvVar, ModelProvider, SettingsResponse } from "@/types";
import { Topbar } from "@/components/layout/Topbar";
import { classNames } from "@/lib/utils";

export default function SettingsPage() {
  const [data, setData] = useState<SettingsResponse>(SETTINGS);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionStatus | null>(null);

  useEffect(() => {
    api.get<SettingsResponse>("/settings").then(setData).catch(() => {});
  }, []);

  const selected =
    data.providers.find((p) => p.id === data.selectedProviderId) ?? data.providers[0];

  async function pickProvider(id: string) {
    await api.patch(`/settings/provider/${id}`);
    setData((prev) => ({ ...prev, selectedProviderId: id }));
  }

  async function testConnection() {
    setTesting(true);
    try {
      const res = await api.post<ConnectionStatus>("/settings/test-connection");
      setTestResult(res);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar title="设置面板" badge={{ label: "本地配置", tone: "neutral" }} />
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto max-w-[1640px] space-y-3">
          <div className="flex items-center gap-3">
            <div className="flex-1" />
            <button
              type="button"
              className="h-10 px-3.5 rounded-lg bg-accent text-white text-[13.5px] font-semibold inline-flex items-center gap-1.5 hover:bg-accent-hover"
            >
              保存修改
            </button>
          </div>
          <div className="grid grid-cols-[1fr_360px] gap-3">
            <FormPanel
              providers={data.providers}
              selectedId={data.selectedProviderId}
              env={data.env}
              showKey={showKey}
              onToggleKey={() => setShowKey((v) => !v)}
              onPickProvider={pickProvider}
            />
            <SidePanel
              connection={testResult ?? data.connection}
              testing={testing}
              onTest={testConnection}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function FormPanel({
  providers,
  selectedId,
  env,
  showKey,
  onToggleKey,
  onPickProvider,
}: {
  providers: ModelProvider[];
  selectedId: string;
  env: EnvVar[];
  showKey: boolean;
  onToggleKey: () => void;
  onPickProvider: (id: string) => void;
}) {
  const selected = providers.find((p) => p.id === selectedId) ?? providers[0];
  return (
    <div className="rounded-lg border border-line bg-white p-5 space-y-4">
      <SectionLabel>服务商</SectionLabel>
      <div className="space-y-2">
        {providers.map((p) => {
          const isSel = p.id === selectedId;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onPickProvider(p.id)}
              className={classNames(
                "w-full h-[54px] px-3.5 rounded-lg flex items-center gap-2 border transition-colors",
                isSel
                  ? "bg-accent-soft border-accent-ring"
                  : "bg-app-soft border-line hover:bg-line/50",
              )}
            >
              <span
                className={classNames(
                  "text-[14px] font-bold",
                  isSel ? "text-accent-hover" : "text-ink",
                )}
              >
                {p.name.replace("（推荐）", "")}
              </span>
              {p.recommended ? (
                <span className="px-1.5 py-0.5 rounded-md bg-accent text-white text-[10.5px] font-extrabold">
                  推荐
                </span>
              ) : null}
              <span className="flex-1" />
              <ChevronDown className="w-4 h-4 text-ink-muted" />
            </button>
          );
        })}
      </div>

      <Field
        label="API 密钥"
        value={showKey ? "sk-prod-jhs8Hf-EXAMPLEKEY-1298abcd" : selected.apiKeyMasked}
        mono
        right={
          <button
            type="button"
            onClick={onToggleKey}
            className="text-ink-muted hover:text-ink"
            aria-label="切换显示"
          >
            {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          </button>
        }
      />
      <Field label="基础 URL" value={selected.baseUrl} mono />
      <div className="grid grid-cols-[1fr_220px] gap-3">
        <Field label="模型" value={selected.model} mono />
        <Field label="超时" value={`${selected.timeoutSec} 秒`} mono />
      </div>

      <SectionLabel>环境变量</SectionLabel>
      <div className="rounded-lg border border-line bg-app-soft p-3 space-y-2">
        {env.map((e) => (
          <div
            key={e.key}
            className="rounded-md border border-line bg-white h-10 px-2.5 flex items-center gap-2.5"
          >
            <span className="font-mono text-[12px] font-bold text-ink">{e.key}</span>
            <span className="flex-1" />
            <span className="text-ink-muted text-[12px]">{e.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SidePanel({
  connection,
  testing,
  onTest,
}: {
  connection: ConnectionStatus;
  testing: boolean;
  onTest: () => void;
}) {
  return (
    <div className="rounded-lg border border-line bg-white p-5 space-y-3.5">
      <h3 className="text-[18px] font-bold font-display">连接状态</h3>
      <div className="rounded-lg bg-success-soft p-3.5 flex items-center gap-3 border border-success/10">
        <CircleCheck className="w-5 h-5 text-success" />
        <div>
          <div className="text-success-deep text-[14px] font-extrabold">已连接</div>
          <div className="text-success-deep/80 text-[12px]">{connection.testedAt}</div>
        </div>
      </div>
      <div className="rounded-lg border border-line bg-app-soft p-3.5 space-y-1.5">
        <div className="text-ink text-[13px] font-extrabold">最近一次测试</div>
        <p className="text-ink-muted text-[12px] leading-[1.45]">
          状态码 {connection.statusCode} · 延迟 {connection.latencyMs}ms · deepseek-chat 可用
        </p>
      </div>
      <button
        type="button"
        onClick={onTest}
        disabled={testing}
        className="w-full h-10 rounded-lg border border-line-strong bg-app-soft inline-flex items-center justify-center gap-1.5 text-ink text-[14px] font-semibold hover:bg-line/50 disabled:opacity-60"
      >
        <RefreshCw className={classNames("w-3.5 h-3.5", testing ? "animate-spin" : "")} />
        {testing ? "测试中…" : "测试连接"}
      </button>
    </div>
  );
}

function Field({
  label,
  value,
  mono,
  right,
}: {
  label: string;
  value: string;
  mono?: boolean;
  right?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-line bg-app-soft p-3 space-y-1.5">
      <div className="text-ink-muted text-[11px] font-extrabold">{label}</div>
      <div className={classNames("flex items-center gap-2", mono ? "font-mono" : "")}>
        <span className="text-[13.5px] text-ink truncate">{value}</span>
        <span className="flex-1" />
        {right}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-ink-muted text-[12px] font-extrabold tracking-wide">{children}</div>
  );
}
