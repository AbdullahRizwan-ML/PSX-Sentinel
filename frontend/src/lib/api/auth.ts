import { apiRequest, tokenStorage } from "./client";
import type {
  TokenResponse,
  UserLoginRequest,
  UserRegisterRequest,
  UserResponse,
} from "./types";

export async function register(payload: UserRegisterRequest): Promise<TokenResponse> {
  const tokens = await apiRequest<TokenResponse>("/api/v1/auth/register", {
    method: "POST",
    body: payload,
    auth: false,
  });
  tokenStorage.setTokens(tokens);
  return tokens;
}

export async function login(payload: UserLoginRequest): Promise<TokenResponse> {
  const tokens = await apiRequest<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: payload,
    auth: false,
  });
  tokenStorage.setTokens(tokens);
  return tokens;
}

export async function logout(): Promise<void> {
  const refresh = tokenStorage.getRefresh();
  if (refresh) {
    try {
      await apiRequest("/api/v1/auth/logout", {
        method: "POST",
        body: { refresh_token: refresh },
        auth: false,
      });
    } catch {
      // best-effort — blacklist failure should not block local logout
    }
  }
  tokenStorage.clear();
}

export async function getCurrentUser(): Promise<UserResponse> {
  return apiRequest<UserResponse>("/api/v1/auth/me");
}
