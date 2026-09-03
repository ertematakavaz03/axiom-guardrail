const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string, public reasonCode?: string) { super(message); }
}

export function token(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem("agentarena_token");
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const accessToken = token();
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body;
    if (response.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("agentarena_token");
    }
    throw new ApiError(response.status, detail.message ?? "Request failed", detail.reason_code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function saveSession(accessToken: string): void {
  localStorage.setItem("agentarena_token", accessToken);
}

export function clearSession(): void {
  localStorage.removeItem("agentarena_token");
}
