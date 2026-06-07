export interface TestEmailInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

/** Optional test-email override (plan #19): route outreach drafts to this address. */
export function TestEmailInput({ value, onChange, disabled }: TestEmailInputProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="test-email" className="text-sm font-medium">
        Test email
      </label>
      <input
        id="test-email"
        type="email"
        placeholder="optional — route drafts here"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-md border border-input bg-background px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
      />
    </div>
  );
}
