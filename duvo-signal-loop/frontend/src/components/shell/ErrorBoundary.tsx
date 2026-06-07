import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Optional custom fallback; receives the error and a reset callback. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * App-wide render-error trap (plan #18). Catches exceptions thrown during a
 * child's render/lifecycle so one broken view never blanks the whole SPA, and
 * offers a retry that re-mounts the subtree. Network/async errors are handled
 * by TanStack Query + toasts; this is the last-resort boundary for render bugs.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No telemetry boundary in the SPA yet; surface to the dev console only.
    console.error("ErrorBoundary caught a render error", error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.reset);
      return (
        <div
          role="alert"
          className="mx-auto mt-16 max-w-md space-y-4 rounded-xl border border-destructive/50 bg-destructive/10 p-6 text-center"
        >
          <div className="space-y-1">
            <h2 className="text-base font-semibold">Something went wrong</h2>
            <p className="text-sm text-muted-foreground">
              An unexpected error broke this view. You can retry without reloading.
            </p>
          </div>
          <Button variant="outline" onClick={this.reset}>
            Try again
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
