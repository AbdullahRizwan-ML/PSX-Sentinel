/*
 * The single chokepoint for every frontend → backend call.
 *
 * Mirrors the backend's own LLMGateway philosophy: one place owns transport,
 * auth, error normalization, and token refresh. Components must NEVER call
 * fetch() directly — they import from @/lib/api/* and let this client handle
 * the access/refresh token dance.
 *
 * Token storage strategy: tokens live in localStorage. JWT-in-localStorage
 * has well-known XSS trade-offs, but for a portfolio/demo app shipping in
 * Phase 4 Session 1 this is the pragmatic call. Refactor to httpOnly cookies
 * during the deployment-hardening pass before any real users.
 */

import type { ApiErrorBody, TokenResponse } from "./types";

const ACCESS_KEY = "psx_access_token";
const REFRESH_KEY = "psx_refresh_token";

export const tokenStorage = {
  getAccess(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(ACCESS_KEY);
  },
  getRefresh(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(REFRESH_KEY);
  },
  setTokens(tokens: TokenResponse) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  },
  clear() {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  },
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody | undefined;
  constructor(status: number, message: string, body?: ApiErrorBody) {
    super(message);
    this.status = status;
    this.body = body;
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  auth?: boolean;
  // Internal flag to prevent infinite refresh loops
  _retry?: boolean;
}

let refreshPromise: Promise<TokenResponse | null> | null = null;

async function refreshAccessToken(): Promise<TokenResponse | null> {
  const refresh = tokenStorage.getRefresh();
  if (!refresh) return null;
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        tokenStorage.clear();
        return null;
      }
      const tokens = (await res.json()) as TokenResponse;
      tokenStorage.setTokens(tokens);
      return tokens;
    } catch {
      tokenStorage.clear();
      return null;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const { method = "GET", body, auth = true, _retry = false } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };

  if (auth) {
    const access = tokenStorage.getAccess();
    if (access) headers["Authorization"] = `Bearer ${access}`;
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(
      0,
      err instanceof Error
        ? `Network error: ${err.message}`
        : "Network error — is the backend running?",
    );
  }

  // Auto-refresh on 401 once
  if (res.status === 401 && auth && !_retry) {
    const newTokens = await refreshAccessToken();
    if (newTokens) {
      return apiRequest<T>(path, { ...options, _retry: true });
    }
  }

  if (!res.ok) {
    let errBody: ApiErrorBody | undefined;
    try {
      errBody = (await res.json()) as ApiErrorBody;
    } catch {
      // body wasn't JSON
    }
    const detail =
      errBody?.detail ||
      `Request failed (${res.status} ${res.statusText})`;
    throw new ApiError(res.status, detail, errBody);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}
