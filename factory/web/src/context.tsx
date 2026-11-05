import { createContext, useCallback, useContext, useSyncExternalStore, type ReactNode } from "react";
import { Navigate, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiFailure, type Api } from "./api.ts";
import type { Auth } from "./auth.ts";
import type { ConnectionSource, ConnectionState } from "./connection.ts";

export interface Services {
  auth: Auth;
  api: Api;
  connection: ConnectionSource;
  // 一覧と詳細を読み直す間隔（ミリ秒）。無ければ既定の 30 秒
  pollIntervalMs?: number;
  // API の入口。委任の画面が MCP のクライアントへの登録の例に出す
  apiUrl?: string;
}

export const ServicesContext = createContext<Services | null>(null);

export function useServices(): Services {
  const s = useContext(ServicesContext);
  if (!s) throw new Error("ServicesContext is missing");
  return s;
}

export function useConnection(): ConnectionState {
  const { connection } = useServices();
  return useSyncExternalStore(connection.subscribe, connection.current, connection.current);
}

// 画面に出す文。403 はアカウントの有無を出さない
export function failureText(err: unknown, t: TFunction): string {
  if (err instanceof ApiFailure) {
    if (err.status === 403) return t("failure.forbidden");
    if (err.status === 409 || err.status === 422) return err.message;
    if (err.status === 400) return err.message || t("failure.checkInput");
  }
  return t("failure.retryLater");
}

// API の 401（token が無い・期限切れ）ではログインの画面へ移る。それ以外の失敗は文で返す
export function useFailureHandler(): (err: unknown) => string | null {
  const navigate = useNavigate();
  const { t } = useTranslation();
  return useCallback(
    (err: unknown) => {
      if (err instanceof ApiFailure && err.status === 401) {
        navigate("/login", { replace: true });
        return null;
      }
      return failureText(err, t);
    },
    [navigate, t],
  );
}

// ログインの後の画面の枠を包む。token が無ければ枠も画面も描かずにログインの画面へ移る
export function RequireLogin({ children }: { children: ReactNode }) {
  const { auth } = useServices();
  if (auth.getToken() === null) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export type Attempt = <T>(op: () => Promise<T>, show: (message: string | null) => void) => Promise<void>;

// op を呼び、失敗したら画面の文を show へ渡す（401 ならログインの画面へ移って null）。attempt は投げない。
// 描くたびに同じ関数を返す（useEffect の依存に入れても読み込みを繰り返さない）
export function useAttempt(): Attempt {
  const onFailure = useFailureHandler();
  return useCallback(
    async (op, show) => {
      try {
        await op();
      } catch (err) {
        show(onFailure(err));
      }
    },
    [onFailure],
  ) as Attempt;
}
