import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { api, ApiError, type Me } from "@/lib/api";

/** Stable cache key for the current-user query, reusable for invalidation on sign-out. */
export const ME_QUERY_KEY = ["me"] as const;

/**
 * Fetch the authenticated user from `GET /me` (plan #13).
 *
 * A `401` is the *unauthenticated* state, not a transient failure — we never
 * retry it (that would hammer the endpoint while signed out). Other errors are
 * retried once via the app-wide QueryClient default; the {@link AuthGuard}
 * distinguishes 401 (-> login screen) from other errors (-> error fallback).
 */
export function useMe(): UseQueryResult<Me, ApiError> {
  return useQuery<Me, ApiError>({
    queryKey: ME_QUERY_KEY,
    queryFn: () => api.me(),
    // 401 is the terminal "signed out" state — never retry it. Other errors are
    // retried once (transient server/network blips) with a short backoff.
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 401) && failureCount < 1,
    retryDelay: 200,
    staleTime: 5 * 60 * 1000,
  });
}
