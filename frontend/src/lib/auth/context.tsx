"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { getCurrentUser, login as apiLogin, logout as apiLogout, register as apiRegister } from "@/lib/api/auth";
import { tokenStorage } from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import type { UserResponse } from "@/lib/api/types";

interface AuthContextValue {
  user: UserResponse | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<UserResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const router = useRouter();

  const refresh = React.useCallback(async () => {
    if (!tokenStorage.getAccess() && !tokenStorage.getRefresh()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await getCurrentUser();
      setUser(me);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        tokenStorage.clear();
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = React.useCallback(
    async (email: string, password: string) => {
      await apiLogin({ email, password });
      const me = await getCurrentUser();
      setUser(me);
    },
    []
  );

  const register = React.useCallback(
    async (email: string, password: string, fullName: string) => {
      await apiRegister({ email, password, full_name: fullName });
      const me = await getCurrentUser();
      setUser(me);
    },
    []
  );

  const logout = React.useCallback(async () => {
    await apiLogout();
    setUser(null);
    router.push("/login");
  }, [router]);

  const value: AuthContextValue = {
    user,
    loading,
    login,
    register,
    logout,
    refresh,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return ctx;
}
