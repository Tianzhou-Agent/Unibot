import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AinaWidget } from "@/types";
import { BridgeConfirmDialog, BridgeError, isRecord, PACKAGE_SANDBOX, packageUiUrl, useAinaBridge } from "./ainaBridge";

const MAX_HEIGHT = 480;

/**
 * One AINA 2.0 widget in the conversation (design §9.2). It is a pure view of its input: everything it shows comes
 * from the archived call and result, so it re-renders the same after a reload. A confirmation widget can only
 * propose arguments; approving stays a host control outside the frame.
 */
export function AinaWidgetFrame({
  widget,
  onProposeArguments,
  onPrompt,
  onOpenAina,
}: {
  widget: AinaWidget;
  onProposeArguments?: (args: Record<string, unknown>) => void;
  onPrompt?: (prompt: string) => void;
  onOpenAina?: (ainaId: string) => void;
}) {
  const { t } = useTranslation("canvas");
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(Math.min(widget.height, MAX_HEIGHT));
  const { confirmation, decide, error } = useAinaBridge({
    ainaId: widget.aina_id,
    frame,
    origin: null,
    source: "widget",
    context: {
      mode: "widget",
      aina: { id: widget.aina_id, version: widget.version },
      purpose: widget.purpose,
      tool: widget.tool_name,
      arguments: widget.arguments,
      result: widget.purpose === "confirmation" ? null : { content: widget.content, structuredContent: widget.structured_content },
    },
    onPrompt,
    extra: (method, params) => {
      switch (method) {
        case "resize": {
          const requested = Number(params.height);
          if (!Number.isFinite(requested) || requested <= 0) return Promise.reject(new BridgeError("INVALID_REQUEST", "height must be a positive number"));
          setHeight(Math.min(Math.ceil(requested), MAX_HEIGHT));
          return Promise.resolve({ height: Math.min(Math.ceil(requested), MAX_HEIGHT) });
        }
        case "proposeArguments":
          if (widget.purpose !== "confirmation" || !onProposeArguments) {
            return Promise.reject(new BridgeError("PERMISSION_DENIED", t("ainaFrame.proposeOnly")));
          }
          if (!isRecord(params.arguments)) return Promise.reject(new BridgeError("INVALID_REQUEST", "arguments must be an object"));
          onProposeArguments(params.arguments);
          return Promise.resolve({});
        case "openCanvas":
          onOpenAina?.(widget.aina_id);
          return Promise.resolve({});
        default:
          return undefined;
      }
    },
  });

  return (
    <div className="overflow-hidden rounded-xl border border-line-strong bg-white">
      {error ? <p role="alert" className="px-3 py-2 text-[12px] text-danger-deep">{error}</p> : null}
      <iframe
        ref={frame}
        title={t("ainaFrame.widgetTitle", { tool: widget.tool_name })}
        src={packageUiUrl(widget.aina_id, widget.version, widget.entry)}
        sandbox={PACKAGE_SANDBOX}
        referrerPolicy="no-referrer"
        loading="lazy"
        style={{ height }}
        className="block w-full border-0"
      />
      <BridgeConfirmDialog appName={widget.aina_id} confirmation={confirmation} onDecide={decide} />
    </div>
  );
}
