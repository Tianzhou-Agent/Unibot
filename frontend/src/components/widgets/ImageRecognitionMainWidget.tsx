import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clipboard,
  Cpu,
  Gauge,
  ImagePlus,
  Loader2,
  RefreshCw,
  ScanSearch,
  Upload,
  X,
} from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { VisionDetectionResponse, VisionHealth } from "@/types";

const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const BOX_COLORS = ["#2563eb", "#e11d48", "#16a34a", "#9333ea", "#ea580c", "#0891b2"];

export function ImageRecognitionMainWidget() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const requestIdRef = useRef(0);
  const [health, setHealth] = useState<VisionHealth | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [result, setResult] = useState<VisionDetectionResponse | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkHealth = useCallback(async () => {
    setHealthError(null);
    try {
      setHealth(await api.get<VisionHealth>("/vision/health"));
    } catch (reason) {
      setHealth(null);
      setHealthError(apiErrorMessage(reason));
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  const detect = useCallback(async (file: File) => {
    const requestId = ++requestIdRef.current;
    setDetecting(true);
    setError(null);
    setResult(null);
    const form = new FormData();
    form.append("image", file, file.name || "clipboard-image.png");
    try {
      const response = await api.postForm<VisionDetectionResponse>("/vision/detect", form);
      if (requestId === requestIdRef.current) setResult(response);
    } catch (reason) {
      if (requestId === requestIdRef.current) setError(apiErrorMessage(reason));
    } finally {
      if (requestId === requestIdRef.current) setDetecting(false);
    }
  }, []);

  const chooseFile = useCallback(
    (file: File) => {
      if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
        setError("仅支持 JPEG、PNG 或 WebP 图片。");
        return;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        setError("图片不能超过 10 MB。");
        return;
      }
      setPreviewUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return URL.createObjectURL(file);
      });
      setSelectedFile(file);
      void detect(file);
    },
    [detect],
  );

  useEffect(() => {
    function handlePaste(event: ClipboardEvent) {
      const image = Array.from(event.clipboardData?.items ?? [])
        .find((item) => item.kind === "file" && item.type.startsWith("image/"))
        ?.getAsFile();
      if (!image) return;
      event.preventDefault();
      chooseFile(new File([image], image.name || "clipboard-image.png", { type: image.type }));
    }
    window.addEventListener("paste", handlePaste);
    return () => window.removeEventListener("paste", handlePaste);
  }, [chooseFile]);

  function clearImage() {
    requestIdRef.current += 1;
    setPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return null;
    });
    setSelectedFile(null);
    setResult(null);
    setError(null);
    setDetecting(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div className="h-full overflow-y-auto bg-surface-subtle">
      <div className="mx-auto flex min-h-full max-w-6xl flex-col gap-3 p-4">
        <header className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-white px-4 py-3 shadow-sm">
          <div>
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-soft text-brand">
                <ScanSearch className="h-4.5 w-4.5" />
              </span>
              <div>
                <h2 className="text-[15px] font-extrabold text-ink">YOLO26m 目标检测</h2>
                <p className="text-[11.5px] text-ink-muted">粘贴或选择图片，自动识别目标、位置与置信度</p>
              </div>
            </div>
          </div>
          <HealthBadge health={health} error={healthError} onRetry={checkHealth} />
        </header>

        <div className="grid min-h-[560px] flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
          <section className="flex min-h-[480px] flex-col overflow-hidden rounded-xl border border-line bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div>
                <h3 className="text-[13px] font-bold text-ink">图片预览</h3>
                {selectedFile ? (
                  <p className="mt-0.5 text-[10.5px] text-ink-muted">
                    {selectedFile.name} · {formatFileSize(selectedFile.size)}
                  </p>
                ) : null}
              </div>
              {selectedFile ? (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={detecting}
                    onClick={() => void detect(selectedFile)}
                    className="btn-outline !px-3 !py-1.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <RefreshCw className={classNames("h-3.5 w-3.5", detecting && "animate-spin")} />
                    重新识别
                  </button>
                  <button
                    type="button"
                    onClick={clearImage}
                    aria-label="清除图片"
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-line text-ink-muted transition hover:border-danger/30 hover:bg-danger/5 hover:text-danger"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : null}
            </div>

            <div
              className={classNames(
                "relative flex flex-1 items-center justify-center overflow-hidden p-4",
                !previewUrl && "m-4 rounded-xl border-2 border-dashed border-line bg-surface-subtle",
              )}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const file = event.dataTransfer.files[0];
                if (file) chooseFile(file);
              }}
            >
              {previewUrl ? (
                <div className="relative inline-block max-h-full max-w-full overflow-hidden rounded-lg bg-[#0b1220] shadow-inner">
                  <img
                    src={previewUrl}
                    alt="待识别图片预览"
                    className="block max-h-[660px] max-w-full object-contain"
                  />
                  {result ? (
                    <svg
                      viewBox={`0 0 ${result.image.width} ${result.image.height}`}
                      preserveAspectRatio="none"
                      className="pointer-events-none absolute inset-0 h-full w-full"
                      aria-label="目标检测框"
                    >
                      {result.detections.map((detection, index) => {
                        const color = BOX_COLORS[index % BOX_COLORS.length];
                        const width = Math.max(0, detection.box.x2 - detection.box.x1);
                        const height = Math.max(0, detection.box.y2 - detection.box.y1);
                        return (
                          <g key={`${detection.class_id}-${index}`} data-testid={`detection-box-${index}`}>
                            <rect
                              x={detection.box.x1}
                              y={detection.box.y1}
                              width={width}
                              height={height}
                              fill={`${color}22`}
                              stroke={color}
                              strokeWidth={Math.max(2, result.image.width / 350)}
                            />
                            <rect
                              x={detection.box.x1}
                              y={Math.max(0, detection.box.y1 - 24)}
                              width={Math.max(72, detection.label_zh.length * 18 + 58)}
                              height={24}
                              fill={color}
                            />
                            <text
                              x={detection.box.x1 + 5}
                              y={Math.max(16, detection.box.y1 - 7)}
                              fill="white"
                              fontSize={14}
                              fontWeight={700}
                            >
                              {detection.label_zh} {Math.round(detection.confidence * 100)}%
                            </text>
                          </g>
                        );
                      })}
                    </svg>
                  ) : null}
                  {detecting ? (
                    <div className="absolute inset-0 flex items-center justify-center bg-[#081225]/70 text-white backdrop-blur-[1px]">
                      <div className="flex items-center gap-2 rounded-lg bg-black/35 px-4 py-2 text-[12px] font-semibold">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        正在识别…
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex max-w-sm flex-col items-center rounded-xl px-8 py-10 text-center transition hover:bg-white"
                >
                  <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-soft text-brand">
                    <ImagePlus className="h-7 w-7" />
                  </span>
                  <strong className="text-[14px] text-ink">粘贴、拖入或选择图片</strong>
                  <span className="mt-2 text-[11.5px] leading-5 text-ink-muted">
                    支持 JPEG、PNG、WebP
                    <br />
                    单张图片最大 10 MB
                    <br />
                    图片仅用于本次识别，不会持久化保存
                  </span>
                  <span className="btn-primary mt-4">
                    <Upload className="h-4 w-4" />
                    选择图片
                  </span>
                </button>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                aria-label="选择识别图片"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) chooseFile(file);
                }}
              />
            </div>
          </section>

          <aside className="flex min-h-[480px] flex-col overflow-hidden rounded-xl border border-line bg-white shadow-sm">
            <div className="border-b border-line px-4 py-3">
              <div className="flex items-center gap-2">
                <Gauge className="h-4 w-4 text-brand" />
                <h3 className="text-[13px] font-bold text-ink">识别结果</h3>
                {result ? (
                  <span className="rounded-full bg-brand-soft px-2 py-0.5 text-[10px] font-bold text-brand">
                    {result.detections.length} 个目标
                  </span>
                ) : null}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-3">
              {error ? (
                <div className="rounded-lg border border-danger/20 bg-danger/5 p-3 text-[11.5px] leading-5 text-danger">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{error}</span>
                  </div>
                </div>
              ) : detecting ? (
                <div className="flex h-full min-h-48 flex-col items-center justify-center text-center text-ink-muted">
                  <Loader2 className="mb-3 h-7 w-7 animate-spin text-brand" />
                  <strong className="text-[12px] text-ink">YOLO26m 正在分析图片</strong>
                  <span className="mt-1 text-[11px]">首次运行可能需要加载模型</span>
                </div>
              ) : result ? (
                <DetectionResults result={result} />
              ) : (
                <div className="flex h-full min-h-48 flex-col items-center justify-center px-5 text-center text-ink-muted">
                  <Clipboard className="mb-3 h-7 w-7 text-ink-faint" />
                  <strong className="text-[12px] text-ink">等待图片</strong>
                  <span className="mt-1 text-[11px] leading-5">
                    可直接按 Ctrl+V 粘贴剪贴板中的截图，也可以从本地选择图片
                  </span>
                </div>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

function HealthBadge({
  health,
  error,
  onRetry,
}: {
  health: VisionHealth | null;
  error: string | null;
  onRetry: () => Promise<void>;
}) {
  if (error) {
    return (
      <button type="button" onClick={() => void onRetry()} className="flex items-center gap-2 rounded-lg bg-danger/5 px-3 py-2 text-[11px] text-danger">
        <AlertCircle className="h-3.5 w-3.5" />
        服务不可用，点击重试
      </button>
    );
  }
  if (!health) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-surface-subtle px-3 py-2 text-[11px] text-ink-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        检查识别服务
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-lg bg-success/10 px-3 py-2 text-[11px] font-semibold text-success">
      <CheckCircle2 className="h-3.5 w-3.5" />
      {health.model} · {health.device.startsWith("cuda") ? health.gpu_name || "GPU" : "CPU"}
    </div>
  );
}

function DetectionResults({ result }: { result: VisionDetectionResponse }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <ResultMetric icon={<ScanSearch className="h-3.5 w-3.5" />} label="检测目标" value={`${result.detections.length} 个`} />
        <ResultMetric icon={<Gauge className="h-3.5 w-3.5" />} label="推理耗时" value={`${Math.round(result.inference_ms)} ms`} />
        <ResultMetric icon={<Cpu className="h-3.5 w-3.5" />} label="运行设备" value={deviceLabel(result.device)} />
        <ResultMetric icon={<ImagePlus className="h-3.5 w-3.5" />} label="图片尺寸" value={`${result.image.width} × ${result.image.height}`} />
      </div>

      {result.detections.length ? (
        <div className="space-y-2">
          {result.detections.map((detection, index) => {
            const color = BOX_COLORS[index % BOX_COLORS.length];
            return (
              <div key={`${detection.class_id}-${index}`} className="rounded-lg border border-line p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: color }} />
                    <div className="min-w-0">
                      <div className="truncate text-[12px] font-bold text-ink">{detection.label_zh}</div>
                      <div className="truncate text-[10px] text-ink-muted">{detection.label}</div>
                    </div>
                  </div>
                  <span className="text-[12px] font-extrabold text-brand">{(detection.confidence * 100).toFixed(1)}%</span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-subtle">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${detection.confidence * 100}%`, backgroundColor: color }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-line bg-surface-subtle p-4 text-center text-[11.5px] text-ink-muted">
          未检测到置信度足够的目标
        </div>
      )}
    </div>
  );
}

function ResultMetric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-subtle p-2.5">
      <div className="flex items-center gap-1.5 text-[10px] text-ink-muted">
        {icon}
        {label}
      </div>
      <div className="mt-1 truncate text-[11.5px] font-bold text-ink" title={value}>{value}</div>
    </div>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function deviceLabel(device: string): string {
  return device.startsWith("cuda") ? "GPU" : "CPU";
}
