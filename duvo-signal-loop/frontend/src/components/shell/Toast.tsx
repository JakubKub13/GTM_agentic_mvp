import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export type ToastVariant = "default" | "success" | "error";

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: ToastVariant;
  /** Auto-dismiss after this many ms; `0` disables auto-dismiss. Default 5000. */
  duration?: number;
}

interface ToastRecord extends Required<Pick<ToastOptions, "title" | "variant">> {
  id: number;
  description?: string;
  duration: number;
}

interface ToastContextValue {
  /** Push a toast; returns its id so callers can dismiss it explicitly. */
  toast: (options: ToastOptions) => number;
  /** Dismiss a single toast by id, or all toasts when called with no id. */
  dismiss: (id?: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_DURATION = 5000;

const variantClasses: Record<ToastVariant, string> = {
  default: "border bg-background text-foreground",
  success: "border-emerald-500/40 bg-emerald-50 text-emerald-900",
  error: "border-destructive/50 bg-destructive/10 text-destructive",
};

/**
 * App-wide toast host (plan #18, used for the cross-cutting error toasts of #22).
 * Part of the app shell: wrap the app once (next to {@link AppLayout}); consume
 * via {@link useToast}. Error toasts are assertive `alert`s; informational and
 * success toasts are polite `status`es, so screen readers handle each correctly.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const clearTimer = useCallback((id: number) => {
    const handle = timers.current.get(id);
    if (handle !== undefined) {
      clearTimeout(handle);
      timers.current.delete(id);
    }
  }, []);

  const dismiss = useCallback(
    (id?: number) => {
      if (id === undefined) {
        timers.current.forEach((handle) => clearTimeout(handle));
        timers.current.clear();
        setToasts([]);
        return;
      }
      clearTimer(id);
      setToasts((prev) => prev.filter((t) => t.id !== id));
    },
    [clearTimer],
  );

  const toast = useCallback(
    (options: ToastOptions) => {
      const id = nextId.current++;
      const record: ToastRecord = {
        id,
        title: options.title,
        description: options.description,
        variant: options.variant ?? "default",
        duration: options.duration ?? DEFAULT_DURATION,
      };
      setToasts((prev) => [...prev, record]);
      if (record.duration > 0) {
        const handle = setTimeout(() => dismiss(id), record.duration);
        timers.current.set(id, handle);
      }
      return id;
    },
    [dismiss],
  );

  // Clear any pending timers if the provider unmounts.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((handle) => clearTimeout(handle));
      pending.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
        aria-label="Notifications"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: ToastRecord; onDismiss: () => void }) {
  const isError = toast.variant === "error";
  return (
    <div
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      className={cn(
        "pointer-events-auto flex items-start gap-3 rounded-md p-3 shadow-lg",
        variantClasses[toast.variant],
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{toast.title}</p>
        {toast.description ? (
          <p className="mt-0.5 text-sm opacity-80">{toast.description}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="shrink-0 rounded-md p-1 opacity-60 transition-opacity hover:opacity-100"
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}

/** Access the toast API. Throws if used outside a {@link ToastProvider}. */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return ctx;
}
