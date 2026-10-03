"use client";

import { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { AuthLayout } from "@/components/auth-layout";
import { GoogleSignInButton } from "@/components/google-sign-in-button";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "Google sign-in could not be verified. Please try again.",
  token_error: "Google sign-in could not be completed. Please try again.",
  missing_sub: "Google account verification failed. Please try again.",
  unverified_email: "Your Google email could not be verified.",
  identity_conflict: "This Google account could not be linked to the existing CodeLens account.",
  google_denied: "Google sign-in was cancelled.",
  missing_params: "Google sign-in failed due to missing parameters.",
  missing_state: "Google sign-in state was lost. Please try again.",
  not_configured: "Google sign-in is not currently configured on the server.",
};

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const errCode = searchParams.get("error");
    if (errCode) {
      setError(ERROR_MESSAGES[errCode] || "Authentication failed. Please try again.");
    }
  }, [searchParams]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const user = await api.login({ email, password });
      setUser(user);
      router.replace("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError("Invalid email or password. Please try again.");
        } else {
          setError(err.message || "Sign in failed. Please try again.");
        }
      } else {
        setError("Unable to connect. Please check your network and try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {error && (
        <div className="mb-5 rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label
            htmlFor="email"
            className="block text-sm font-medium text-zinc-300 mb-1.5"
          >
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="cl-input"
          />
        </div>

        <div>
          <label
            htmlFor="password"
            className="block text-sm font-medium text-zinc-300 mb-1.5"
          >
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="cl-input"
          />
        </div>

        <Button type="submit" className="w-full" size="lg" disabled={loading}>
          {loading ? "Signing in..." : "Sign in"}
        </Button>
      </form>

      <div className="mt-6 relative">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-zinc-800"></div>
        </div>
        <div className="relative flex justify-center text-sm">
          <span className="px-2 bg-[#0a0a0a] text-zinc-500">OR</span>
        </div>
      </div>

      <div className="mt-6">
        <GoogleSignInButton />
      </div>

      <p className="mt-6 text-center text-sm text-zinc-400">
        Don&apos;t have an account?{" "}
        <Link
          href="/signup"
          className="font-medium text-cyan-300 hover:text-cyan-200 transition-colors"
        >
          Create one
        </Link>
      </p>
    </>
  );
}

export default function LoginPage() {
  return (
    <AuthLayout title="Sign in">
      <Suspense fallback={<div className="h-64 animate-pulse bg-zinc-900/50 rounded-lg"></div>}>
        <LoginForm />
      </Suspense>
    </AuthLayout>
  );
}
