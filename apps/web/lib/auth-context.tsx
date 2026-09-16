"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { api } from "./api";
import type { UserResponse } from "@/types/api";

interface AuthContextValue {
  user: UserResponse | null;
  /** true while checking session on mount */
  loading: boolean;
  /** refresh the current user from the server */
  refresh: () => Promise<void>;
  /** call after successful login/signup – caller passes returned user */
  setUser: (user: UserResponse | null) => void;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  refresh: async () => {},
  setUser: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.getMe();
      setUser(me);
    } catch {
      setUser(null);
    }
  }, []);

  // Check session on mount
  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ user, loading, refresh, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

