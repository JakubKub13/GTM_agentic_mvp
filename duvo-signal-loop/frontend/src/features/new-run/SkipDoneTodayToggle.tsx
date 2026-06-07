import { Toggle } from "./Toggle";

export interface SkipDoneTodayToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

/** Skip-done-today toggle (plan #19): skip accounts already completed today (real runs only). */
export function SkipDoneTodayToggle({ checked, onChange, disabled }: SkipDoneTodayToggleProps) {
  return (
    <Toggle
      id="skip-done-today"
      label="Skip done today"
      description="Skip domains already processed by a real run today."
      checked={checked}
      onChange={onChange}
      disabled={disabled}
    />
  );
}
