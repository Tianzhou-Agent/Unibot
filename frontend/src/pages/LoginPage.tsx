import { useState, type FormEvent, type ReactNode } from "react";
import { Bot, Github, Loader2, LockKeyhole, Mail, UserRound } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { apiErrorMessage, apiUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { classNames } from "@/lib/utils";

type Mode = "login" | "register";

export default function LoginPage() {
  const { user, loading, config, login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const from = ((location.state as { from?: string } | null)?.from ?? "/chat");

  if (!loading && user) return <Navigate to={from} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(name, email, password);
      navigate(from, { replace: true });
    } catch (reason) {
      setError(apiErrorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#071126] px-4 py-10">
      <div className="pointer-events-none absolute -left-24 top-[-180px] h-[420px] w-[420px] rounded-full bg-accent/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-48 right-[-80px] h-[480px] w-[480px] rounded-full bg-cyan-400/10 blur-3xl" />

      <div className="relative grid w-full max-w-4xl overflow-hidden rounded-3xl border border-white/10 bg-white shadow-2xl lg:grid-cols-[0.9fr_1.1fr]">
        <section className="hidden bg-[#0b1831] p-10 text-white lg:flex lg:flex-col lg:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <img src="/unibot-icon-v2.png" alt="" className="h-11 w-11 rounded-xl" />
              <div>
                <div className="text-xl font-extrabold">Unibot</div>
                <div className="text-xs text-white/55">智能体运行平台</div>
              </div>
            </div>
            <h1 className="mt-16 text-3xl font-extrabold leading-tight">登录你的智能工作空间</h1>
            <p className="mt-4 text-sm leading-7 text-white/60">
              对话、记忆、AINA、文档和运行容器都按用户安全隔离，在任意节点登录后继续工作。
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-white/45">
            <Bot className="h-4 w-4" />
            本地账户与 GitHub OAuth
          </div>
        </section>

        <section className="p-6 sm:p-10">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-3">
              <img src="/unibot-icon-v2.png" alt="" className="h-10 w-10 rounded-xl" />
              <div className="text-lg font-extrabold text-ink">Unibot</div>
            </div>
          </div>

          <div className="mb-7 flex rounded-xl bg-app-soft p-1">
            {(["login", "register"] as Mode[]).map((item) => (
              <button
                key={item}
                type="button"
                disabled={item === "register" && !config.registration_enabled}
                onClick={() => { setMode(item); setError(null); }}
                className={classNames(
                  "flex-1 rounded-lg px-4 py-2.5 text-sm font-bold transition",
                  mode === item ? "bg-white text-ink shadow-sm" : "text-ink-muted hover:text-ink",
                  item === "register" && !config.registration_enabled && "cursor-not-allowed opacity-40",
                )}
              >
                {item === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>

          <div className="mb-6">
            <h2 className="text-2xl font-extrabold text-ink">{mode === "login" ? "欢迎回来" : "创建账户"}</h2>
            <p className="mt-1.5 text-sm text-ink-muted">
              {mode === "login" ? "登录后继续你的 AINA 工作。" : "注册后将自动创建独立用户空间。"}
            </p>
          </div>

          {config.github_enabled ? (
            <>
              <a
                href={apiUrl(`/auth/github?next=${encodeURIComponent(from)}`)}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-line bg-white text-sm font-bold text-ink transition hover:bg-app-soft"
              >
                <Github className="h-4.5 w-4.5" />
                使用 GitHub 登录
              </a>
              <div className="my-5 flex items-center gap-3 text-[11px] text-ink-subtle">
                <span className="h-px flex-1 bg-line" />或使用邮箱<span className="h-px flex-1 bg-line" />
              </div>
            </>
          ) : null}

          <form onSubmit={submit} className="space-y-4">
            {mode === "register" ? (
              <Field icon={<UserRound className="h-4 w-4" />} label="昵称">
                <input
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  autoComplete="name"
                  placeholder="怎么称呼你"
                  className="auth-input"
                />
              </Field>
            ) : null}
            <Field icon={<Mail className="h-4 w-4" />} label="邮箱">
              <input
                required
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                placeholder="name@example.com"
                className="auth-input"
              />
            </Field>
            <Field icon={<LockKeyhole className="h-4 w-4" />} label="密码">
              <input
                required
                type="password"
                minLength={mode === "register" ? 8 : 1}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder={mode === "register" ? "至少 8 个字符" : "输入密码"}
                className="auth-input"
              />
            </Field>

            {error ? <div className="rounded-lg border border-danger/20 bg-danger/5 px-3 py-2.5 text-xs text-danger">{error}</div> : null}

            <button type="submit" disabled={submitting} className="btn-primary !mt-6 h-11 w-full justify-center disabled:opacity-60">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {mode === "login" ? "登录" : "注册并进入"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}

function Field({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1.5 text-xs font-bold text-ink-muted">{icon}{label}</span>
      {children}
    </label>
  );
}
