import { useTranslation } from "react-i18next";
import { useState, type FormEvent, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { apiErrorMessage, apiUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { classNames } from "@/lib/utils";

type Mode = "login" | "register";

export default function LoginPage() {
  const { t } = useTranslation("login");
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
  const registrationEnabled = config.registration_enabled && !config.huawei_enabled;
  const registering = mode === "register" && registrationEnabled;
  const oauthProviders = [
    { id: "google", enabled: config.google_enabled, className: "border border-[#747775] bg-white text-[#1f1f1f] hover:bg-app-soft" },
    { id: "microsoft", enabled: config.microsoft_enabled, className: "bg-[#0067b8] text-white hover:bg-[#005da6]" },
    { id: "apple", enabled: config.apple_enabled, className: "bg-black text-white hover:bg-[#1f1f1f]" },
    { id: "github", enabled: config.github_enabled, className: "bg-[#24292f] text-white hover:bg-[#1b1f24]" },
    { id: "huawei", enabled: config.huawei_enabled, className: "bg-[#c8102e] text-white hover:bg-[#ae0d27]" },
  ].filter((provider) => provider.enabled);

  if (loading) {
    return <div className="flex min-h-screen items-center justify-center bg-[#f7f9fc] text-sm text-ink-muted">{t("common:auth.checking")}</div>;
  }
  if (user) return <Navigate to={from} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (registering) await register(name, email, password);
      else await login(email, password);
      navigate(from, { replace: true });
    } catch (reason) {
      setError(apiErrorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f7f9fc] px-4 py-10">
      <section className="w-full max-w-[410px] rounded-[18px] border border-[#e3e8f0] bg-white px-6 py-8 sm:px-[34px] sm:py-[38px]" aria-labelledby="login-title">
        <h1 id="login-title" className="flex items-center justify-center gap-2.5 text-center text-[25px] font-bold leading-snug text-ink">
          <img src={`${import.meta.env.BASE_URL}unibot-icon-v2.png`} alt="" className="h-9 w-9 shrink-0 rounded-[10px]" />
          <span>{registering ? t("createAccount") : t("welcomeBack")}</span>
        </h1>
        <p className="mb-7 mt-2.5 text-center text-[13px] leading-relaxed text-ink-muted">
          {registering ? t("registerHint") : t("loginHint")}
        </p>

        <form onSubmit={submit} className="space-y-[18px]">
          {registering ? (
            <Field label={t("nickname")}>
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="name"
                className="auth-input !h-[50px] !rounded-[10px] !border-[#e3e8f0] !text-base"
              />
            </Field>
          ) : null}
          <Field label={t("email")}>
            <input
              required
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              className="auth-input !h-[50px] !rounded-[10px] !border-[#e3e8f0] !text-base"
            />
          </Field>
          <Field label={t("password")}>
            <input
              required
              type="password"
              minLength={registering ? 8 : 1}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={registering ? "new-password" : "current-password"}
              placeholder={registering ? t("passwordRegisterPlaceholder") : undefined}
              className="auth-input !h-[50px] !rounded-[10px] !border-[#e3e8f0] !text-base"
            />
          </Field>

          {error ? <div role="alert" className="rounded-lg border border-danger/20 bg-danger/5 px-3 py-2.5 text-xs text-danger">{error}</div> : null}

          <button type="submit" disabled={submitting} className="btn-primary !mt-6 !h-[50px] w-full justify-center !rounded-[10px] disabled:opacity-60">
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {registering ? t("submitRegister") : t("submitLogin")}
          </button>
        </form>

        {oauthProviders.length > 0 ? (
          <>
            <div className="my-[22px] flex items-center gap-3.5 text-xs text-ink-muted">
              <span className="h-px flex-1 bg-[#e3e8f0]" />{t("or")}<span className="h-px flex-1 bg-[#e3e8f0]" />
            </div>
            <div className="space-y-3">
              {oauthProviders.map(({ id, className }) => (
                <a
                  key={id}
                  href={apiUrl(`/auth/${id}?next=${encodeURIComponent(from)}`)}
                  className={classNames(
                    "flex min-h-[50px] w-full items-center justify-center gap-3 rounded-[10px] px-4 py-3 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                    className,
                  )}
                >
                  <img src={`${import.meta.env.BASE_URL}oauth/${id}.svg`} alt="" className={classNames("h-5 w-5 shrink-0", ["apple", "github", "huawei"].includes(id) && "brightness-0 invert", id === "microsoft" && "rounded-sm bg-white p-0.5")} />
                  <span>{t(id)}</span>
                </a>
              ))}
            </div>
          </>
        ) : null}

        {registrationEnabled ? (
          <button type="button" onClick={() => { setMode(registering ? "login" : "register"); setError(null); }} className="mx-auto mt-4 block min-h-11 px-2 text-sm text-accent hover:text-accent-hover">
            {registering ? t("backToLogin") : t("tab.register")}
          </button>
        ) : null}
      </section>
    </main>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-2 block text-[13px] text-ink">{label}</span>
      {children}
    </label>
  );
}
