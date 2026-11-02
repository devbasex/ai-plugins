import { createContext, useCallback, useContext, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiFailure, type Api } from "./api.ts";
import type { Auth } from "./auth.ts";
import type { ConnectionSource, ConnectionState } from "./connection.ts";

export interface Services {
  auth: Auth;
  api: Api;
  connection: ConnectionSource;
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
