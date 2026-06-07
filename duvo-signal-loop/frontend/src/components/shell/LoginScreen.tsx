import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Same-origin base, mirroring `lib/api` (a split deploy overrides via VITE_API_BASE). */
const API_BASE: string = import.meta.env?.VITE_API_BASE ?? "";

/**
 * URL that starts the Google OAuth flow (plan #13: `GET /auth/login` ->
 * Google consent with `state`). Sign-in is a full-page server redirect, not a
 * `fetch`, so the login button is a plain anchor.
 */
export function loginUrl(): string {
  return `${API_BASE}/auth/login`;
}

export interface LoginScreenProps {
  /** Optional context for *why* the user is here (e.g. "Your session expired."). */
  reason?: string;
}

/**
 * Unauthenticated landing screen (plan #18). A single Google sign-in button
 * that navigates to the backend OAuth start; the backend handles consent,
 * token validation, allowlist enforcement, and the session cookie.
 */
export function LoginScreen({ reason }: LoginScreenProps) {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>duvo signal loop</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Sign in to trigger runs and watch them execute live.
          </p>
          {reason ? (
            <p role="status" className="text-sm text-muted-foreground">
              {reason}
            </p>
          ) : null}
          <a
            href={loginUrl()}
            className={cn(buttonVariants({ variant: "default" }), "w-full")}
          >
            <GoogleMark />
            Sign in with Google
          </a>
        </CardContent>
      </Card>
    </main>
  );
}

/** Inline Google "G" mark so the button is self-contained (no asset pipeline). */
function GoogleMark() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.42 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z"
      />
    </svg>
  );
}
