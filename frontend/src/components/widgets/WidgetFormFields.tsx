import type { Dispatch, SetStateAction } from "react";
import type { WidgetDefinition } from "@/types";

export function WidgetFormFields({
  widget,
  disabled,
  values,
  setValues,
}: {
  widget: WidgetDefinition;
  disabled: boolean;
  values: Record<string, string>;
  setValues: Dispatch<SetStateAction<Record<string, string>>>;
}) {
  if (!widget.fields.length) return null;

  return (
    <div className="space-y-3">
      {widget.fields.map((field) => (
        <label key={field.id} className="block space-y-1.5">
          <span className="text-[11.5px] font-bold text-ink">
            {field.label}{field.required ? <span className="ml-1 text-danger">*</span> : null}
          </span>
          {field.input_type === "textarea" ? (
            <textarea
              value={values[field.id] ?? ""}
              onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))}
              disabled={disabled}
              rows={5}
              aria-label={field.label}
              placeholder={field.placeholder}
              className="input-soft resize-y text-[12.5px]"
            />
          ) : (
            <input
              value={values[field.id] ?? ""}
              onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))}
              disabled={disabled}
              type={field.input_type}
              aria-label={field.label}
              placeholder={field.placeholder}
              className="input-soft text-[12.5px]"
            />
          )}
        </label>
      ))}
    </div>
  );
}
