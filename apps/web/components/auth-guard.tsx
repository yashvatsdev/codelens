"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Loader2 } from "lucide-react";

/**
 * Wraps any page that requires authentication.
 * While the session is loading, renders nothing (avoids flash).
 * When determined to be unauthenticated, redirects to /login.
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  // Show nothing while resolving session to avoid flashing dashboard
  if (loading || !user) {
    return (
      <div className="min-h-screen bg-[#090a0b] flex items-center justify-center">
        <div className="flex items-center gap-3 text-zinc-400 text-sm cl-fade-up">
          <Loader2 className="size-5 animate-spin text-cyan-300" />
          Authenticating…
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
