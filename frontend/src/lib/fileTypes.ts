import { FileCode2, FileSpreadsheet, FileText, type LucideIcon } from "lucide-react";

// The files Unibot reads (backend aina/document/file_types.py); it refuses any other upload.
const MARKDOWN = [".md"];
const PROSE = [".markdown", ".txt", ".text", ".log", ".rst"];
const SHEETS = [".csv", ".tsv"];
const CODE = [
  ".json", ".jsonl", ".yaml", ".yml", ".xml", ".html", ".htm", ".toml", ".ini", ".cfg", ".conf",
  ".sql", ".py", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss",
  ".java", ".kt", ".scala", ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".rb", ".php", ".swift",
  ".dart", ".lua", ".r", ".sh", ".bash", ".ps1", ".bat", ".diff", ".patch",
];
const BINARY = [".pdf", ".docx", ".xlsx"];
export const READABLE_EXTENSIONS = [...MARKDOWN, ...PROSE, ...SHEETS, ...CODE, ...BINARY];

/** How the file tree treats a file: Markdown has sections, text is edited whole, binary is read-only. */
export type DocumentKind = "markdown" | "text" | "binary";

export function fileExtension(name: string): string {
  const base = name.slice(name.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

export function isReadableFile(name: string): boolean {
  return READABLE_EXTENSIONS.includes(fileExtension(name));
}

export function documentKind(name: string): DocumentKind {
  const extension = fileExtension(name);
  if (BINARY.includes(extension)) return "binary";
  return MARKDOWN.includes(extension) || !extension ? "markdown" : "text";
}

export interface FileVisual {
  icon: LucideIcon;
  /** Short type label, such as PDF or CSV. */
  label: string;
  /** Classes of the icon tile: a light fill with a matching dark icon. */
  tile: string;
}

export function fileVisual(name: string): FileVisual {
  const extension = fileExtension(name);
  const label = extension.slice(1).toUpperCase() || "MD";
  if (extension === ".pdf") return { icon: FileText, label, tile: "bg-red-50 text-red-600" };
  if (extension === ".docx") return { icon: FileText, label, tile: "bg-blue-50 text-blue-600" };
  if (extension === ".xlsx" || SHEETS.includes(extension)) return { icon: FileSpreadsheet, label, tile: "bg-emerald-50 text-emerald-700" };
  if (CODE.includes(extension)) return { icon: FileCode2, label, tile: "bg-violet-50 text-violet-600" };
  return { icon: FileText, label, tile: "bg-slate-100 text-slate-600" };
}

const ACE_MODES: Record<string, string> = {
  ".md": "markdown", ".markdown": "markdown", ".rst": "rst", ".csv": "csv", ".tsv": "tsv",
  ".json": "json", ".jsonl": "json", ".ipynb": "json", ".yaml": "yaml", ".yml": "yaml", ".xml": "xml",
  ".html": "html", ".htm": "html", ".vue": "vue", ".svelte": "html", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
  ".sql": "sql", ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
  ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx", ".css": "css", ".scss": "scss", ".java": "java",
  ".kt": "kotlin", ".scala": "scala", ".go": "golang", ".rs": "rust", ".c": "c_cpp", ".h": "c_cpp",
  ".cpp": "c_cpp", ".cc": "c_cpp", ".hpp": "c_cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
  ".swift": "swift", ".dart": "dart", ".lua": "lua", ".r": "r", ".sh": "sh", ".bash": "sh",
  ".ps1": "powershell", ".bat": "batchfile", ".diff": "diff", ".patch": "diff",
};

/** The code editor's syntax mode for a text file. */
export function aceMode(name: string): string {
  return ACE_MODES[fileExtension(name)] ?? "text";
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
