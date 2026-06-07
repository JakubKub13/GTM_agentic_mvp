import { useState } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { AuthGuard } from "@/auth";
import { ToastProvider as CommonToastProvider } from "@/components/common";
import { AppLayout, ErrorBoundary, ToastProvider as ShellToastProvider } from "@/components/shell";
import { AccountDetailDrawer } from "@/features/account-detail";
import { LiveRunView } from "@/features/live-run";
import { NewRunForm } from "@/features/new-run";

/**
 * Live Run route (plan #20/#21): reads :runId, wires the account drill-down drawer.
 * Client path is `/run/:runId` (singular) so it never shadows the API's `GET /runs/{id}`
 * — a hard reload / deep-link falls through to the SPA static fallback (index.html).
 */
function LiveRunRoute() {
  const { runId = "" } = useParams();
  const [domain, setDomain] = useState<string | null>(null);
  return (
    <>
      <LiveRunView runId={runId} onSelectAccount={setDomain} />
      <AccountDetailDrawer
        runId={runId}
        domain={domain}
        open={domain !== null}
        onClose={() => setDomain(null)}
      />
    </>
  );
}

/**
 * App composition (plan #18): ErrorBoundary → AuthGuard → AppLayout → routes.
 * Both ToastProvider contexts are mounted because the shell and common Toast are
 * distinct modules (common's is used by NewRunForm, shell's by AppLayout).
 */
export default function App() {
  return (
    <ErrorBoundary>
      <CommonToastProvider>
        <ShellToastProvider>
          <AuthGuard>
            <AppLayout>
              <Routes>
                <Route path="/" element={<NewRunForm />} />
                <Route path="/run/:runId" element={<LiveRunRoute />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppLayout>
          </AuthGuard>
        </ShellToastProvider>
      </CommonToastProvider>
    </ErrorBoundary>
  );
}
