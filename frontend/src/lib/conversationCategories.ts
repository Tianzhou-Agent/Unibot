export const CONVERSATION_CATEGORIES = [
  { value: "general", label: "未分类" },
  { value: "work", label: "工作" },
  { value: "personal", label: "个人" },
  { value: "project", label: "项目" },
] as const;

export function conversationCategoryLabel(value: string): string {
  return CONVERSATION_CATEGORIES.find((item) => item.value === value)?.label ?? value;
}
