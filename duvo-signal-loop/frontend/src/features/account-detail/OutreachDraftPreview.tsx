import type { OutreachDraft } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/common";

export interface OutreachDraftPreviewProps {
  /** The router's queued-for-review outreach draft (duvo/models.OutreachDraft). */
  draft: OutreachDraft | null;
}

/**
 * Read-only preview of the queued outreach draft (plan #21).
 *
 * Outreach is queue-for-review only (never auto-sent, plan #14); this drawer
 * shows the persona, subject, first line and body. Editing/sending lands in the
 * Phase 2 approval cockpit (out of scope here).
 */
export function OutreachDraftPreview({ draft }: OutreachDraftPreviewProps) {
  if (!draft) {
    return (
      <EmptyState
        title="No outreach draft"
        description="The router did not produce a draft for this account."
      />
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{draft.subject}</CardTitle>
        <span className="text-xs text-muted-foreground">To: {draft.persona}</span>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm font-medium">{draft.first_line}</p>
        <p className="whitespace-pre-wrap text-sm text-muted-foreground">{draft.body}</p>
      </CardContent>
    </Card>
  );
}
