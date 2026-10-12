"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ApiError, api } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [firm, setFirm] = useState("northstar-legal");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(firm, email, password);
      // Only same-site paths: never redirect to an absolute URL taken from the query string.
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid email or password."
          : err instanceof ApiError && err.status === 429
            ? "Too many failed attempts for this account. Try again in 15 minutes."
            : "Sign-in failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Intake review: staff sign-in</h1>
      <p className="muted">Local demo with fictional data only.</p>
      <form onSubmit={submit}>
        <label>
          Firm
          <input value={firm} onChange={(e) => setFirm(e.target.value)} required autoComplete="organization" />
        </label>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </label>
        <button type="submit" disabled={busy}>
          Sign in
        </button>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
      </form>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
