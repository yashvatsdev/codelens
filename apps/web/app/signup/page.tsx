"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { AuthLayout } from "@/components/auth-layout";

export default function SignupPage() {
  const router = useRouter();
  const { setUser } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const user = await api.signup({
        email,
        password,
        name: name.trim() || undefined,
      });
      setUser(user);
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setError(
            "An account with this email already exists. Try signing in.",
          );
        } else if (err.status === 422) {
          setError(
            "Please provide a valid email and a password of at least 8 characters.",
          );
        } else {
          setError(err.message || "Sign up failed. Please try again.");
        }
      } else {
        setError("Unable to connect. Please check your network and try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthLayout title="Create account">
      {error && (
        <div className="mb-5 rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label
            htmlFor="name"
            className="block text-sm font-medium text-zinc-300 mb-1.5"
          >
            Name <span className="text-zinc-500 font-normal">(optional)</span>
          </label>
          <input
            id="name"
            type="text"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="cl-input"
          />
        </div>

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
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Min. 8 characters"
            className="cl-input"
          />
        </div>

        <Button type="submit" className="w-full" size="lg" disabled={loading}>
          {loading ? "Creating account..." : "Create account"}
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-zinc-400">
        Already have an account?{" "}
        <Link
          href="/login"
          className="font-medium text-cyan-300 hover:text-cyan-200 transition-colors"
        >
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
