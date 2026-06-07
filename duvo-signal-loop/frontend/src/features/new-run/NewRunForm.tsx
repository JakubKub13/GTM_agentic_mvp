import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog, useToast } from "@/components/common";
import type { Company, StartRunRequest } from "@/lib/api";

import { ConcurrencyInput } from "./ConcurrencyInput";
import { DryRunToggle } from "./DryRunToggle";
import { FileDropzone } from "./FileDropzone";
import { SkipDoneTodayToggle } from "./SkipDoneTodayToggle";
import { TestEmailInput } from "./TestEmailInput";
import { useStartRun } from "./useStartRun";

/**
 * New Run form (plan #19): assembles the dropzone + inputs into a
 * {@link StartRunRequest} and POSTs via {@link useStartRun}. The dry-run toggle
 * defaults ON; launching a real (side-effecting) run is gated by a confirm
 * dialog before any POST fires.
 */
export function NewRunForm() {
  const { toast } = useToast();
  const { mutate, isPending } = useStartRun();

  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [concurrency, setConcurrency] = useState<number | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [skipDoneToday, setSkipDoneToday] = useState(false);
  const [testEmail, setTestEmail] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  function buildBody(): StartRunRequest {
    const email = testEmail.trim();
    return {
      dry_run: dryRun,
      // A real (non-dry) launch only reaches launch() after the confirm dialog, so the
      // backend's #14 gate (allowlisted role + explicit confirmation) is satisfied.
      confirm: !dryRun,
      concurrency,
      skip_done_today: skipDoneToday,
      test_email: email === "" ? null : email,
      companies,
    };
  }

  function launch() {
    setConfirmOpen(false);
    mutate(buildBody(), {
      onError: (err) => {
        toast({
          title: "Could not start the run",
          description: err.message,
          variant: "error",
        });
      },
    });
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (isPending) return;
    // Real (non-dry) runs cause CRM/Slack side effects — gate behind a confirm (plan #14).
    if (!dryRun) {
      setConfirmOpen(true);
      return;
    }
    launch();
  }

  return (
    <Card className="mx-auto mt-12 max-w-lg">
      <CardHeader>
        <CardTitle>New run</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="space-y-6" onSubmit={onSubmit}>
          <FileDropzone companies={companies} onChange={setCompanies} disabled={isPending} />
          <ConcurrencyInput value={concurrency} onChange={setConcurrency} disabled={isPending} />
          <DryRunToggle checked={dryRun} onChange={setDryRun} disabled={isPending} />
          <SkipDoneTodayToggle
            checked={skipDoneToday}
            onChange={setSkipDoneToday}
            disabled={isPending}
          />
          <TestEmailInput value={testEmail} onChange={setTestEmail} disabled={isPending} />
          <Button type="submit" className="w-full" disabled={isPending}>
            {isPending ? "Starting…" : "Start run"}
          </Button>
        </form>
      </CardContent>

      <ConfirmDialog
        open={confirmOpen}
        destructive
        title="Launch a real run?"
        description="This is NOT a dry run. It writes records to the CRM and posts Slack alerts. Outreach drafts are queued for review, never auto-sent."
        confirmLabel="Launch real run"
        cancelLabel="Keep dry-run"
        pending={isPending}
        onConfirm={launch}
        onCancel={() => setConfirmOpen(false)}
      />
    </Card>
  );
}
