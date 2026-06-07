import { useQuery } from "@tanstack/react-query";

import { api, type AccountDetail } from "@/lib/api";

/** Query key for an account's drill-down detail. */
export function accountDetailKey(runId: string, domain: string | null) {
  return ["account-detail", runId, domain] as const;
}

/**
 * Fetch a single account's drill-down detail from
 * `GET /runs/{id}/accounts/{domain}` (plan #21).
 *
 * Disabled (no fetch) until a `domain` is selected, so the drawer can mount the
 * hook unconditionally and only fetch when opened.
 */
export function useAccountDetail(runId: string, domain: string | null) {
  return useQuery<AccountDetail>({
    queryKey: accountDetailKey(runId, domain),
    queryFn: () => api.getAccount(runId, domain as string),
    enabled: Boolean(runId && domain),
  });
}
