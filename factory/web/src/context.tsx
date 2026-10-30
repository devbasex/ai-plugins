import { createContext, useCallback, useContext } from "react";
import { useNavigate } from "react-router";
import { ApiFailure, type Api } from "./api.ts";
import type { Auth } from "./auth.ts";

export interface Services {
  auth: Auth;
  api: Api;
}

export const ServicesContext = createContext<Services | null>(null);

export function useServices(): Services {
  const s = useContext(ServicesContext);
  if (!s) throw new Error("ServicesContext がありません");
  return s;
}

// 画面に出す文。403 はアカウントの有無を出さない
export function failureText(err: unknown): string {
  if (err instanceof ApiFailure) {
    if (err.status === 403) return "このアカウントを見る権限がありません";
    if (err.status === 409 || err.status === 422) return err.message;
    if (err.status === 400) return err.message || "入力を確かめてください";
  }
  return "時間をおいてやり直してください";
}

// API の 401（token が無い・期限切れ）ではログインの画面へ移る。それ以外の失敗は文で返す
export function useFailureHandler(): (err: unknown) => string | null {
  const navigate = useNavigate();
  return useCallback(
    (err: unknown) => {
      if (err instanceof ApiFailure && err.status === 401) {
        navigate("/login", { replace: true });
        return null;
      }
      return failureText(err);
    },
    [navigate],
  );
}
