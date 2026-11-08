// Session: bearer token in sessionStorage only (cleared with the tab); the user profile is
// re-fetched from /auth/me on load, never persisted. Role checks here only shape the UI -
// every permission is enforced again by the backend.
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, type Role, UNAUTHORIZED_EVENT, type User } from "../api";

export const TOKEN_KEY = "opspilot.token";

interface Session {
  token: string | null;
  user: User | null;
  loading: boolean;
  expired: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => void;
  can: (...roles: Role[]) => boolean;
}

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState(false);

  const logout = useCallback(() => {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    const saved = sessionStorage.getItem(TOKEN_KEY);
    if (!saved) {
      setLoading(false);
      return;
    }
    api
      .me(saved)
      .then((u) => {
        setUser(u);
        setToken(saved);
      })
      .catch(() => sessionStorage.removeItem(TOKEN_KEY))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const on = () => {
      if (sessionStorage.getItem(TOKEN_KEY)) setExpired(true);
      logout();
    };
    window.addEventListener(UNAUTHORIZED_EVENT, on);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, on);
  }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    const me = await api.me(res.access_token);
    sessionStorage.setItem(TOKEN_KEY, res.access_token);
    setExpired(false);
    setToken(res.access_token);
    setUser(me);
    return me;
  }, []);

  const value = useMemo<Session>(
    () => ({ token, user, loading, expired, login, logout, can: (...roles) => !!user && roles.includes(user.role) }),
    [token, user, loading, expired, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

/** For pages rendered only when signed in. */
export function useAuth(): Session & { token: string; user: User } {
  const s = useSession();
  if (!s.token || !s.user) throw new Error("useAuth requires a signed-in session");
  return s as Session & { token: string; user: User };
}
