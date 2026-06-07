import { useEffect } from "react";
import { X } from "lucide-react";

import type { ICPScore, OutreachDraft, Signal } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { LoadingSkeleton } from "@/components/common";
import { cn } from "@/lib/utils";

import { useAccountDetail } from "./useAccountDetail";
import { ScoreCard } from "./ScoreCard";
import { SignalList } from "./SignalList";
import { OutreachDraftPreview } from "./OutreachDraftPreview";
import { DiffBadge } from "./DiffBadge";
import { AgentLogViewer } from "./AgentLogViewer";

export interface AccountDetailDrawerProps {
  /** The run the account belongs to. */
  runId: string;
  /** The selected account's domain; null closes/empties the drawer. */
  domain: string | null;
  /** Whether the drawer is open. */
  open: boolean;
  /** Called to dismiss the drawer (close button / Escape / backdrop). */
  onClose: () => void;
}

/** Parse account_runs.score_json (a JSON object) defensively into an ICPScore. */
function parseScore(scoreJson: string | null | undefined): ICPScore | null {
  if (!scoreJson) return null;
  try {
    const parsed: unknown = JSON.parse(scoreJson);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const obj = parsed as Record<string, unknown>;
    if (typeof obj.score !== "number") return null; // empty "{}" placeholder
    return obj as unknown as ICPScore;
  } catch {
    return null;
  }
}

/** Parse account_runs.signals_json (a JSON array) defensively into Signal[]. */
function parseSignals(signalsJson: string | null | undefined): Signal[] {
  if (!signalsJson) return [];
  try {
    const parsed: unknown = JSON.parse(signalsJson);
    return Array.isArray(parsed) ? (parsed as Signal[]) : [];
  } catch {
    return [];
  }
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/**
 * Account drill-down drawer (plan #21).
 *
 * Reads `GET /runs/{id}/accounts/{domain}` and composes ScoreCard
 * (why_fit/why_not), SignalList (source links, dates, type),
 * OutreachDraftPreview, DiffBadge (▲+N vs last run) and AgentLogViewer.
 * Loading/error/empty states degrade gracefully; malformed persisted JSON
 * falls back to per-section empty states rather than crashing the drawer.
 */
export function AccountDetailDrawer({ runId, domain, open, onClose }: AccountDetailDrawerProps) {
  const { data, isLoading, isError } = useAccountDetail(runId, open ? domain : null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !domain) return null;

  const account = data?.account;
  const score = parseScore(account?.score_json);
  const signals = parseSignals(account?.signals_json);
  const outreach: OutreachDraft | null = score?.outreach ?? null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="Close drawer"
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
      />
      <aside
        className={cn(
          "relative z-10 flex h-full w-full max-w-xl flex-col gap-4 overflow-y-auto",
          "border-l bg-background p-6 shadow-xl",
        )}
      >
        <header className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold">{account?.company_name ?? domain}</h2>
            <p className="text-sm text-muted-foreground">{domain}</p>
            {account ? <DiffBadge score={account.score} diff={data!.diff} /> : null}
          </div>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </header>

        {isLoading ? (
          <LoadingSkeleton lines={6} />
        ) : isError || !account ? (
          <p className="text-sm text-destructive">Couldn't load this account's detail.</p>
        ) : (
          <div className="space-y-6">
            <ScoreCard score={score} />
            <Section title="Signals">
              <SignalList signals={signals} />
            </Section>
            <Section title="Outreach draft">
              <OutreachDraftPreview draft={outreach} />
            </Section>
            <Section title="Agent log">
              <AgentLogViewer agentLogJson={account.agent_log_json} />
            </Section>
          </div>
        )}
      </aside>
    </div>
  );
}
