import { LogIn, Sparkles } from "lucide-react";
import { FormEvent, useState } from "react";
import { login } from "../auth";

export default function Login({ onAuthenticated }: { onAuthenticated: (token: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const token = await login(username.trim(), password);
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
          Username
          <input
            type="text"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="Your username"
            autoComplete="username"
            aria-label="Username"
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
        <button disabled={submitting || !username.trim() || !password}>
          <LogIn size={16} /> {submitting ? "Signing in" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
