import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

type CurrentUser = {
  id: string;
  email: string;
  roles: string[];
  permissions: string[];
};

type AuthState = {
  user: CurrentUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasPermission: (code: string) => boolean;
  hasRole: (name: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function api(path: string, init?: RequestInit, retry = true): Promise<any> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include", // send httpOnly access/refresh cookies
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (res.status === 401 && retry && path !== "/auth/refresh") {
    const refreshed = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (refreshed.ok) return api(path, init, false);
  }

  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    try {
      setUser(await api("/auth/me"));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      await api("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      await refreshMe();
    },
    [refreshMe],
  );

  const logout = useCallback(async () => {
    await api("/auth/logout", { method: "POST" });
    setUser(null);
  }, []);

  const hasPermission = useCallback((code: string) => user?.permissions.includes(code) ?? false, [user]);
  const hasRole = useCallback((name: string) => user?.roles.includes(name) ?? false, [user]);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, hasPermission, hasRole }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
