import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { useMe } from "./useMe";
import { LoginScreen } from "@/components/shell/LoginScreen";

export interface AuthGuardProps {
  children: ReactNode;
}

/**
 * Gate that requires an authenticated session (plan #18). It fetches `GET /me`
 * via {@link useMe} and routes on the result:
 *
 * - in flight  -> a neutral loading state (no flash of the login screen),
 * - `401`      -> the {@link LoginScreen} (unauthenticated),
 * - other error -> an error fallback (server/network problem, *not* "signed out"),
 * - success    -> the protected `children`.
 *
 * It deliberately does NOT render the {@link AppLayout}; composition (guard ->
 * layout -> routes) is the app's job so the layout only ever sees a real user.
 */
export function AuthGuard({ children }: AuthGuardProps) {
  const { data: me, isLoading, error } = useMe();

  if (isLoading) {
    return (
      <div
        role="status"
        aria-label="Checking sign-in"
        className="flex min-h-screen items-center justify-center"
      >
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-foreground" />
        <span className="sr-only">Checking sign-in…</span>
      </div>
    );
  }

  if (error) {
    if (error instanceof ApiError && error.status === 401) {
      return <LoginScreen />;
    }
    return (
      <div
        role="alert"
        className="mx-auto mt-16 max-w-md space-y-2 rounded-xl border border-destructive/50 bg-destructive/10 p-6 text-center"
      >
        <h2 className="text-base font-semibold">Couldn’t verify your session</h2>
        <p className="text-sm text-muted-foreground">
          {error.message || "The server returned an unexpected error. Try again shortly."}
        </p>
      </div>
    );
  }

  if (!me) {
    // Defensive: settled query with neither error nor data should not happen,
    // but never leak protected content on an empty user.
    return <LoginScreen />;
  }

  return <>{children}</>;
}
