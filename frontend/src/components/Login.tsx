import { LogIn, Sparkles } from "lucide-react";
import { FormEvent, useState } from "react";
import { login } from "../auth";

export default function Login({ onAuthenticated }: { onAuthenticated: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const token = await login(email.trim(), password);
      onAuthenticated(token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={handleSubmit}>
        <p className="eyebrow">
          <Sparkles size={14} /> Local public-repo RAG console
        </p>
        <h1>Sign in</h1>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
            autoComplete="username"
            aria-label="Email"
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Your password"
            autoComplete="current-password"
            aria-label="Password"
          />
        </label>
        {error && <p className="login-error">{error}</p>}
        <button disabled={submitting || !email.trim() || !password}>
          <LogIn size={16} /> {submitting ? "Signing in" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
