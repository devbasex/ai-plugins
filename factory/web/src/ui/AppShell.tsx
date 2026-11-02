import { useEffect, useRef, useState } from "react";
import { Outlet, matchPath, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import type { Me } from "../api.ts";
import { useConnection, useFailureHandler, useServices } from "../context.tsx";
import type { Role } from "../screens.ts";
import { ConnectionStatus } from "./ConnectionStatus.tsx";
import { NavMenu } from "./NavMenu.tsx";

function currentAccountId(pathname: string): string | null {
  return matchPath("/accounts/:accountId/*", pathname)?.params.accountId ?? matchPath("/accounts/:accountId", pathname)?.params.accountId ?? null;
}

export function roleOf(me: Me | null, accountId: string | null): Role | null {
  if (!me || accountId === null) return null;
  const row = me.accounts.find((a) => a.account_id === accountId);
  if (!row) return null;
  if (row.kind === "personal") return "personal";
  return row.role === "owner" ? "team_owner" : "team_seat";
}

// ログインの後の画面の枠（layout route）。見出しの帯・ナビゲーション・つながりの状態を置く
// api.me() は token ごとに 1 度だけ呼ぶ。経路の accountId が Me に無いときだけ、その accountId につき 1 度呼び直す
// 失敗しても誤りの文を出さない（誤りは画面の側が出す）。401 は既存の扱いでログインへ移る
export function AppShell() {
  const { t } = useTranslation();
  const { auth, api } = useServices();
  const connection = useConnection();
  const onFailure = useFailureHandler();
  const { pathname } = useLocation();
  const token = auth.getToken();
  const routeAccountId = currentAccountId(pathname);
  const [me, setMe] = useState<Me | null>(null);
  const asked = useRef(new Set<string>());

  useEffect(() => {
    if (token === null) return;
    const known = me?.accounts.some((a) => a.account_id === routeAccountId) ?? false;
    const reason = me === null ? `token:${token}` : routeAccountId !== null && !known ? `account:${routeAccountId}` : null;
    if (reason === null || asked.current.has(reason)) return;
    asked.current.add(reason);
    api.me().then(setMe, (err: unknown) => void onFailure(err));
  }, [api, token, me, routeAccountId, onFailure]);

  const accountId = routeAccountId ?? me?.personal_account_id ?? null;
  return (
    <div className="ui-shell">
      <header className="ui-shell__bar">
        <span className="ui-shell__brand">{t("app.name")}</span>
        <NavMenu accountId={accountId} role={roleOf(me, accountId)} />
        <ConnectionStatus state={connection} />
      </header>
      <main className="ui-shell__main">
        <Outlet />
      </main>
    </div>
  );
}
