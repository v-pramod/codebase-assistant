const TOKEN_KEY = "auth_token";

// Kept in sync with API_BASE in api.ts; auth.ts stays free of api.ts imports so
// api.ts can depend on it (token + 401 handling) without an import cycle.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export async function login(username: string, password: string): Promise<string> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error("Invalid username or password.");
  }
  const data = (await response.json()) as { access_token: string; token_type: string };
  setToken(data.access_token);
  return data.access_token;
}
