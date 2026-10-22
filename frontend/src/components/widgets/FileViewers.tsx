import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Code2, FileText, Loader2, Save, Table2 } from "lucide-react";
import AceEditor from "react-ace";
import "ace-builds/src-noconflict/mode-text";
import "ace-builds/src-noconflict/mode-markdown";
import "ace-builds/src-noconflict/mode-rst";
import "ace-builds/src-noconflict/mode-csv";
import "ace-builds/src-noconflict/mode-tsv";
import "ace-builds/src-noconflict/mode-json";
import "ace-builds/src-noconflict/mode-yaml";
import "ace-builds/src-noconflict/mode-xml";
import "ace-builds/src-noconflict/mode-html";
import "ace-builds/src-noconflict/mode-vue";
import "ace-builds/src-noconflict/mode-toml";
import "ace-builds/src-noconflict/mode-ini";
import "ace-builds/src-noconflict/mode-sql";
import "ace-builds/src-noconflict/mode-python";
import "ace-builds/src-noconflict/mode-javascript";
import "ace-builds/src-noconflict/mode-typescript";
import "ace-builds/src-noconflict/mode-tsx";
import "ace-builds/src-noconflict/mode-jsx";
import "ace-builds/src-noconflict/mode-css";
import "ace-builds/src-noconflict/mode-scss";
import "ace-builds/src-noconflict/mode-java";
import "ace-builds/src-noconflict/mode-kotlin";
import "ace-builds/src-noconflict/mode-scala";
import "ace-builds/src-noconflict/mode-golang";
import "ace-builds/src-noconflict/mode-rust";
import "ace-builds/src-noconflict/mode-c_cpp";
import "ace-builds/src-noconflict/mode-csharp";
import "ace-builds/src-noconflict/mode-ruby";
import "ace-builds/src-noconflict/mode-php";
import "ace-builds/src-noconflict/mode-swift";
import "ace-builds/src-noconflict/mode-dart";
import "ace-builds/src-noconflict/mode-lua";
import "ace-builds/src-noconflict/mode-r";
import "ace-builds/src-noconflict/mode-sh";
import "ace-builds/src-noconflict/mode-powershell";
import "ace-builds/src-noconflict/mode-batchfile";
import "ace-builds/src-noconflict/mode-diff";
import "ace-builds/src-noconflict/theme-github";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { api, apiErrorMessage } from "@/lib/api";
import { aceMode, fileExtension, fileVisual } from "@/lib/fileTypes";
import { classNames } from "@/lib/utils";

const SEPARATORS: Record<string, string> = { ".csv": ",", ".tsv": "\t" };
const MAX_TABLE_ROWS = 1000;

/** A text, data or code file, edited whole with syntax highlighting; a CSV or TSV also shows as a table. */
export function TextFileEditor({ name, content, dirty, saving, disabled, onContent, onSave }: {
  name: string;
  content: string;
  dirty: boolean;
  saving: boolean;
  disabled: boolean;
  onContent: (value: string) => void;
  onSave: () => void;
}) {
  const { t } = useTranslation("doc");
  const separator = SEPARATORS[fileExtension(name)];
  const [view, setView] = useState<"code" | "table">("code");
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Toolbar name={name}>
        {separator ? (
          <Toggle
            label={t("viewer.viewAria")}
            options={[
              { id: "code", label: t("viewer.code"), icon: <Code2 className="h-3.5 w-3.5" /> },
              { id: "table", label: t("viewer.table"), icon: <Table2 className="h-3.5 w-3.5" /> },
            ]}
            value={view}
            onChange={(next) => setView(next as "code" | "table")}
          />
        ) : null}
        <button type="button" className="btn-primary ml-auto h-8 w-8 px-0" onClick={onSave}
          disabled={disabled || saving || !dirty} aria-label={t("editor.save")} title={t("editor.save")}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        </button>
      </Toolbar>
      <div className="min-h-0 flex-1">
        {view === "table" && separator ? <DelimitedTable content={content} separator={separator} /> : (
          <AceEditor
            name="unibot-text-file-editor"
            mode={aceMode(name)}
            theme="github"
            value={content}
            onChange={onContent}
            readOnly={disabled || saving}
            width="100%"
            height="100%"
            fontSize={12}
            showPrintMargin={false}
            highlightActiveLine
            setOptions={{ useWorker: false, tabSize: 2 }}
            onLoad={(editor) => editor.textInput.getElement().setAttribute("aria-label", t("viewer.codeAria", { name }))}
          />
        )}
      </div>
    </div>
  );
}

/** A PDF, DOCX, XLSX or PPTX file: read-only. A PDF shows as itself; each also shows the text Superbot extracted from it. */
export function BinaryFileViewer({ name, content, filePath }: {
  name: string;
  /** The text extracted from the file. */
  content: string;
  /** API path of the file's bytes. */
  filePath: string;
}) {
  const { t } = useTranslation("doc");
  const pdf = fileExtension(name) === ".pdf";
  const [view, setView] = useState<"file" | "text">(pdf ? "file" : "text");
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    if (!pdf) return;
    // The PDF is shown from a blob URL, so the browser's own viewer renders it and the file is never served inline.
    let url: string | null = null;
    let cancelled = false;
    setFileUrl(null);
    setFileError(null);
    api.getBlob(filePath).then(({ blob }) => {
      if (cancelled) return;
      url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
      setFileUrl(url);
    }).catch((loadError) => { if (!cancelled) setFileError(apiErrorMessage(loadError)); });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [filePath, pdf]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Toolbar name={name}>
        <span className="rounded bg-app-soft px-1.5 py-0.5 text-[9px] font-bold text-ink-muted">{t("viewer.readOnly")}</span>
        {pdf ? (
          <Toggle
            label={t("viewer.viewAria")}
            options={[
              { id: "file", label: t("viewer.file"), icon: <FileText className="h-3.5 w-3.5" /> },
              { id: "text", label: t("viewer.text"), icon: <Code2 className="h-3.5 w-3.5" /> },
            ]}
            value={view}
            onChange={(next) => setView(next as "file" | "text")}
          />
        ) : null}
      </Toolbar>
      {view === "file" ? (
        <div className="min-h-0 flex-1 bg-app-soft">
          {fileUrl ? <iframe src={fileUrl} title={name} className="h-full w-full border-0" />
            : <div className="flex h-full items-center justify-center text-[11px] text-ink-muted">
              {fileError ?? <Loader2 className="h-5 w-5 animate-spin" aria-label={t("viewer.loading")} />}
            </div>}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto bg-white p-3" aria-label={t("viewer.textAria", { name })}>
          <p className="mb-3 text-[10px] text-ink-subtle">{t("viewer.converted", { type: fileVisual(name).label })}</p>
          <MarkdownContent content={content || t("editor.emptyDoc")} />
        </div>
      )}
    </div>
  );
}

function Toolbar({ name, children }: { name: string; children: ReactNode }) {
  const visual = fileVisual(name);
  const Icon = visual.icon;
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line bg-app-soft px-3 py-1.5">
      <span className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-md", visual.tile)} title={visual.label}>
        <Icon className="h-3.5 w-3.5" aria-hidden />
      </span>
      {children}
    </div>
  );
}

function Toggle({ label, options, value, onChange }: {
  label: string;
  options: { id: string; label: string; icon: ReactNode }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex h-7 items-center rounded-md border border-line bg-white p-0.5" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.id} type="button" onClick={() => onChange(option.id)} aria-pressed={value === option.id}
          aria-label={option.label} title={option.label}
          className={classNames("flex h-6 w-7 items-center justify-center rounded", value === option.id ? "bg-accent-soft text-accent" : "text-ink-muted hover:text-ink")}>
          {option.icon}
        </button>
      ))}
    </div>
  );
}

function DelimitedTable({ content, separator }: { content: string; separator: string }) {
  const { t } = useTranslation("doc");
  const rows = useMemo(() => parseDelimited(content, separator), [content, separator]);
  const [header, ...body] = rows;
  const shown = body.slice(0, MAX_TABLE_ROWS);
  if (!header) return <p className="p-4 text-[11px] text-ink-muted">{t("editor.emptyDoc")}</p>;
  return (
    <div className="h-full overflow-auto">
      <table className="min-w-full border-collapse text-[11px]">
        <thead className="sticky top-0 bg-app-soft">
          <tr>{header.map((cell, index) => <th key={index} className="border-b border-line px-2.5 py-1.5 text-left font-bold text-ink">{cell}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map((row, rowIndex) => (
            <tr key={rowIndex} className="odd:bg-white even:bg-app-soft/60">
              {header.map((_, index) => <td key={index} className="border-b border-line px-2.5 py-1 text-ink-muted">{row[index] ?? ""}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {body.length > shown.length ? <p className="p-2 text-[10px] text-ink-subtle">{t("viewer.tableTruncated", { count: MAX_TABLE_ROWS })}</p> : null}
    </div>
  );
}

/** Rows of a CSV or TSV text, honouring quoted cells. */
export function parseDelimited(text: string, separator: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character !== '"') cell += character;
      else if (text[index + 1] === '"') { cell += '"'; index += 1; }
      else quoted = false;
    } else if (character === '"') quoted = true;
    else if (character === separator) { row.push(cell); cell = ""; }
    else if (character === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += character;
  }
  if (cell || row.length) { row.push(cell.replace(/\r$/, "")); rows.push(row); }
  return rows;
}
