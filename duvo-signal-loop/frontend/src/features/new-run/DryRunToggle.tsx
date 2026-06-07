import { Toggle } from "./Toggle";

export interface DryRunToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

/**
 * Dry-run toggle (plan #14/#19). Defaults ON in `NewRunForm`: a real
 * (side-effecting) run is a deliberate, confirmed action.
 */
export function DryRunToggle({ checked, onChange, disabled }: DryRunToggleProps) {
  return (
    <Toggle
      id="dry-run"
      label="Dry run"
      description="Simulate write-backs (no CRM upserts, no Slack alerts). Keep this on unless you mean it."
      checked={checked}
      onChange={onChange}
      disabled={disabled}
    />
  );
}
