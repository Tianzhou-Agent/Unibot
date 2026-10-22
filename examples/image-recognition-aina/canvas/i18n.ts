import i18n, { type Resource } from "i18next";
import { initReactI18next } from "react-i18next";
import { host } from "@aina-ui";

/**
 * i18n for a first-party canvas, in the shell's locale. The canvas runs in a sandboxed frame without storage, so the
 * language comes from the bridge context rather than the app's language detector.
 */
export async function initCanvasI18n(namespace: string, strings: { en: object; zh: object }) {
  const { locale } = await host.getContext().catch(() => ({ locale: "en" }));
  const resources: Resource = { en: { [namespace]: strings.en }, zh: { [namespace]: strings.zh } };
  await i18n.use(initReactI18next).init({
    resources,
    lng: locale,
    fallbackLng: "en",
    supportedLngs: ["en", "zh"],
    nonExplicitSupportedLngs: true,
    load: "languageOnly",
    defaultNS: namespace,
    interpolation: { escapeValue: false },
  });
  document.documentElement.lang = i18n.resolvedLanguage === "zh" ? "zh-CN" : "en";
}
