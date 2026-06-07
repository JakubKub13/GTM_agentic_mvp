import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export interface ConfirmDialogProps {
  /** Whether the dialog is visible. Renders nothing when false. */
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button as destructive (e.g. delete). */
  destructive?: boolean;
  /** Disable the confirm button while an action is in flight. */
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Generic modal confirmation (plan #22). Used to gate destructive or
 * side-effecting actions — notably the real-run confirmation in `NewRunForm`
 * (plan #19). Escape and the backdrop both trigger `onCancel`.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  pending = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onCancel}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative z-10 w-full max-w-md rounded-xl border bg-background p-6 shadow-lg"
      >
        <h2 className="text-lg font-semibold leading-none tracking-tight">{title}</h2>
        {description ? (
          <p className="mt-2 text-sm text-muted-foreground">{description}</p>
        ) : null}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={onConfirm}
            disabled={pending}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
