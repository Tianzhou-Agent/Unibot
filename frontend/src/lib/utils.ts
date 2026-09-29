import i18n, { currentLocale } from "@/i18n";

export function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const t = new Date(iso).getTime();
  const diff = (now.getTime() - t) / 1000;
  if (diff < 60) return i18n.t("common:time.justNow");
  if (diff < 3600) return i18n.t("common:time.minutesAgo", { count: Math.floor(diff / 60) });
  if (diff < 86400) return i18n.t("common:time.hoursAgo", { count: Math.floor(diff / 3600) });
  if (diff < 604800) return i18n.t("common:time.daysAgo", { count: Math.floor(diff / 86400) });
  return new Date(iso).toLocaleDateString(currentLocale());
}

export function uid(prefix = "id"): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}
