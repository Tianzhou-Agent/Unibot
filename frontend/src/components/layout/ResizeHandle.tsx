import { useRef } from "react";
import { classNames } from "@/lib/utils";

export function ResizeHandle({ label, value, min, max, onChange, className }: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (width: number) => void;
  className?: string;
}) {
  const dragRef = useRef<{ x: number; width: number } | null>(null);
  const resize = (width: number) => onChange(Math.round(Math.min(max, Math.max(min, width))));

  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      tabIndex={0}
      className={classNames("absolute inset-y-0 right-0 z-30 w-1.5 cursor-col-resize touch-none select-none hover:bg-accent/30 focus-visible:bg-accent/30 focus-visible:outline-none", className)}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        dragRef.current = { x: event.clientX, width: value };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current;
        if (drag) resize(drag.width + event.clientX - drag.x);
      }}
      onPointerUp={(event) => {
        dragRef.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => { dragRef.current = null; }}
      onLostPointerCapture={() => { dragRef.current = null; }}
      onKeyDown={(event) => {
        const width = { ArrowLeft: value - 16, ArrowRight: value + 16, Home: min, End: max }[event.key];
        if (width === undefined) return;
        event.preventDefault();
        resize(width);
      }}
    />
  );
}
