"use client";

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";

// ── Types ───────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string | null;
  display_name: string;
  tier: "lite" | "pro" | "max";
  admin: boolean;
  telegram_id?: string | null;
  consent?: boolean;
}

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string, rememberMe?: boolean) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  refreshUser: () => Promise<void>;
  updateProfile: (displayName: string) => Promise<User>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const BASE_URL = "/api";

// ── Provider ────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Try to restore session via refresh token cookie on mount
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${BASE_URL}/auth/refresh`, { method: "POST", credentials: "include" });
        if (res.ok) {
          const data = await res.json();
          setToken(data.access_token);
          // Fetch user profile
          const meRes = await fetch(`${BASE_URL}/auth/me`, {
            headers: { Authorization: `Bearer ${data.access_token}` },
          });
          if (meRes.ok) {
            setUser(await meRes.json());
          }
        }
      } catch {
        // No valid session — that's fine
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const login = useCallback(async (email: string, password: string, rememberMe = false) => {
    const res = await fetch(`${BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password, remember_me: rememberMe }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Login fehlgeschlagen");
    }
    const data = await res.json();
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const register = useCallback(async (email: string, password: string, displayName?: string) => {
    const res = await fetch(`${BASE_URL}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password, display_name: displayName || "" }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Registrierung fehlgeschlagen");
    }
    const data = await res.json();
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    await fetch(`${BASE_URL}/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {});
    setToken(null);
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, { method: "POST", credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setToken(data.access_token);
      } else {
        setToken(null);
        setUser(null);
      }
    } catch {
      setToken(null);
      setUser(null);
    }
  }, []);

  const refreshUser = useCallback(async () => {
    if (!token) return;
    const meRes = await fetch(`${BASE_URL}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (meRes.ok) {
      setUser(await meRes.json());
    }
  }, [token]);

  const updateProfile = useCallback(async (displayName: string): Promise<User> => {
    if (!token) throw new Error("Not authenticated");
    const res = await fetch(`${BASE_URL}/auth/profile`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ display_name: displayName }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Update fehlgeschlagen");
    }
    const updated = await res.json();
    setUser(updated);
    return updated;
  }, [token]);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout, refresh, refreshUser, updateProfile }}>
      {children}
    </AuthContext.Provider>
  );
}

// ── Hook ────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
