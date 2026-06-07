export interface ConcurrencyInputProps {
  /** Current value; `null` means "use the server default". */
  value: number | null;
  onChange: (value: number | null) => void;
  disabled?: boolean;
}

/** Per-run concurrency input (plan #19). Empty = server default; clamped to >= 1. */
export function ConcurrencyInput({ value, onChange, disabled }: ConcurrencyInputProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="concurrency" className="text-sm font-medium">
        Concurrency
      </label>
      <input
        id="concurrency"
        type="number"
        min={1}
        inputMode="numeric"
        placeholder="server default"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => {
          const raw = e.target.value.trim();
          if (raw === "") {
            onChange(null);
            return;
          }
          const n = Number.parseInt(raw, 10);
          onChange(Number.isFinite(n) && n >= 1 ? n : null);
        }}
        className="h-9 rounded-md border border-input bg-background px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
      />
    </div>
  );
}
