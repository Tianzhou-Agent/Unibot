import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LANGUAGES } from "@/i18n";

export function LanguageSwitcher({ className = "" }: { className?: string }) {
  const { t, i18n } = useTranslation("common");
  return (
    <label className={`inline-flex items-center gap-2 text-[11.5px] text-ink ${className}`}>
      <Languages className="h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
      <span className="sr-only">{t("language")}</span>
      <select
        aria-label={t("language")}
        value={i18n.resolvedLanguage}
        onChange={(event) => void i18n.changeLanguage(event.target.value)}
        className="min-w-0 flex-1 rounded-md border border-line bg-white px-1.5 py-1 text-[11.5px] text-ink"
      >
        {LANGUAGES.map(({ code, label }) => (
          <option key={code} value={code}>{label}</option>
        ))}
      </select>
    </label>
  );
}
