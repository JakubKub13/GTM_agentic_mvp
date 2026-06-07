import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, LogOut } from "lucide-react";

import { api } from "@/lib/api";
import { hardRedirect } from "@/auth/navigation";
import { useMe } from "@/auth/useMe";
import { loginUrl } from "./LoginScreen";
import { useToast } from "./Toast";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface AppLayoutProps {
  children: ReactNode;
}

/**
 * Authenticated chrome (plan #18): a top nav with the brand + primary links and
 * a user menu (signed-in identity + sign out). Renders the routed page body in
 * `children`. Assumes it sits inside the {@link AuthGuard}, so a user is present;
 * it still tolerates `me` being momentarily undefined while the query settles.
 */
export function AppLayout({ children }: AppLayoutProps) {
  const { data: me } = useMe();
  const displayName = me?.name?.trim() || me?.email || "Account";

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <nav
          aria-label="Primary"
          className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4"
        >
          <div className="flex items-center gap-6">
            <Link to="/" className="text-sm font-semibold tracking-tight">
              duvo signal loop
            </Link>
            <Link
              to="/"
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              New run
            </Link>
          </div>
          <UserMenu label={displayName} />
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}

/** Self-contained dropdown: trigger shows the user, the panel offers sign out. */
function UserMenu({ label }: { label: string }) {
  const [open, setOpen] = useState(false);
  const { toast } = useToast();
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click and on Escape — basic, dependency-free menu hygiene.
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const signOut = async () => {
    setOpen(false);
    try {
      await api.logout();
    } catch {
      // Even if the server logout call fails, fall through to the login redirect:
      // the cookie may already be invalid and the user wants out.
      toast({
        variant: "error",
        title: "Sign-out had a problem",
        description: "Redirecting you to sign in again.",
      });
    } finally {
      hardRedirect(loginUrl());
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label="Account menu"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="gap-2"
      >
        <span className="max-w-[12rem] truncate text-sm">{label}</span>
        <ChevronDown className="h-4 w-4 opacity-60" aria-hidden="true" />
      </Button>
      {open ? (
        <div
          role="menu"
          aria-label="Account"
          className={cn(
            "absolute right-0 z-50 mt-1 w-44 overflow-hidden rounded-md border bg-background p-1 shadow-lg",
          )}
        >
          <button
            type="button"
            role="menuitem"
            onClick={signOut}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
