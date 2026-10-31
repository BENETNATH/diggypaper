"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  ReactNode,
} from "react";
import * as api from "@/lib/api";
import { User } from "@/lib/api";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string, consent?: boolean) => Promise<User>;
  register: (token: string, password: string, fullName?: string) => Promise<User>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Auth provider: loads the current user (GET /auth/me) on mount,
 * exposes login/register/logout/refresh. The httpOnly JWT cookie is managed by the
 * backend (credentials: include); here we only keep the user in memory.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const u = await api.me();
      setUser(u);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (email: string, password: string, consent?: boolean) => {
      const u = await api.login(email, password, consent);
      setUser(u);
      return u;
    },
    []
  );

  const register = useCallback(
    async (token: string, password: string, fullName?: string) => {
      const u = await api.register(token, password, fullName);
      setUser(u);
      return u;
    },
    []
  );

  const logout = useCallback(() => {
    setUser(null);
    api.logout();
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, loading, login, register, logout, refresh }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
