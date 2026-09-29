import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

export const LANGUAGES = [
  { code: "en", label: "English (US)" },
  { code: "zh", label: "中文" },
] as const;

export type LanguageCode = (typeof LANGUAGES)[number]["code"];

type Bundle = Record<string, Record<string, unknown>>;

// Each locales/<lang>/<namespace>.ts default-exports that namespace's strings.
function loadBundle(modules: Record<string, { default: Record<string, unknown> }>): Bundle {
  const bundle: Bundle = {};
  for (const [path, mod] of Object.entries(modules)) {
    const ns = path.split("/").pop()!.replace(/\.ts$/, "");
    bundle[ns] = mod.default;
  }
  return bundle;
}

const en = loadBundle(import.meta.glob("./locales/en/*.ts", { eager: true }));
const zh = loadBundle(import.meta.glob("./locales/zh/*.ts", { eager: true }));

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { en, zh },
    fallbackLng: "en",
    // Only en / zh are supported; "zh-CN", "en-GB" etc. collapse to their base language.
    supportedLngs: ["en", "zh"],
    nonExplicitSupportedLngs: true,
    load: "languageOnly",
    defaultNS: "common",
    ns: Object.keys(en),
    interpolation: { escapeValue: false },
    detection: {
      // English is the default: only an explicit user choice (stored) switches language.
      order: ["localStorage"],
      lookupLocalStorage: "unibot.language",
      caches: ["localStorage"],
    },
  });

const syncHtmlLang = (lng: string) => {
  document.documentElement.lang = lng === "zh" ? "zh-CN" : "en";
};
syncHtmlLang(i18n.resolvedLanguage ?? "en");
i18n.on("languageChanged", syncHtmlLang);

/** Locale tag for Intl / toLocale*String formatting. */
export function currentLocale(): string {
  return i18n.resolvedLanguage === "zh" ? "zh-CN" : "en-US";
}

export default i18n;
