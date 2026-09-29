import i18n from "@/i18n";

export const CONVERSATION_CATEGORIES = [
  { value: "general", get label() { return i18n.t("common:category.general"); } },
  { value: "work", get label() { return i18n.t("common:category.work"); } },
  { value: "personal", get label() { return i18n.t("common:category.personal"); } },
  { value: "project", get label() { return i18n.t("common:category.project"); } },
] as const;

export function conversationCategoryLabel(value: string): string {
  return CONVERSATION_CATEGORIES.find((item) => item.value === value)?.label ?? value;
}
