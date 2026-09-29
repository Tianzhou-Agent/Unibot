import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** Remounts the tree on language change so module-level i18n lookups (time-ago, labels) refresh too. */
export function LanguageBoundary({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation();
  return <div key={i18n.resolvedLanguage} className="contents">{children}</div>;
}
