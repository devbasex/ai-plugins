import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router";
import type { AccountDetail, Me } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { ExecutionModeView, LimitsView, PLAN_LABELS } from "./parts.tsx";

export function AccountPage() {
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [personal, setPersonal] = useState<AccountDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasToken = auth.getToken() !== null;

  useEffect(() => {
    if (!hasToken) return;
    void (async () => {
      try {
        const m = await api.me();
        setMe(m);
        setPersonal(await api.account(m.personal_account_id));
      } catch (err) {
        setError(onFailure(err));
      }
    })();
  }, [api, hasToken, onFailure]);

  if (!hasToken) return <Navigate to="/login" replace />;
  if (error) return <p role="alert">{error}</p>;
  if (!me || !personal) return <p>読み込み中</p>;
  const teams = me.accounts.filter((a) => a.kind === "team");
  return (
    <main>
      <h1>自分のアカウント</h1>
      <p>
        利用者の ID: <code>{me.user_id}</code>{" "}
        <button type="button" onClick={() => void navigator.clipboard?.writeText(me.user_id)}>
          写す
        </button>
      </p>
      <p>ログインの手段: {me.idp === "google" ? "Google" : "GitHub"}</p>
      <h2>個人のアカウント</h2>
      <p>プラン: {PLAN_LABELS[personal.plan]}</p>
      <ExecutionModeView account={personal} onChanged={setPersonal} />
      <LimitsView limits={personal.limits} />
      <h2>チーム</h2>
      <ul>
        {teams.map((t) => (
          <li key={t.account_id}>
            <Link to={`/accounts/${t.account_id}`}>{t.name}</Link>（{t.role === "owner" ? "持ち主" : t.seat_type}）
          </li>
        ))}
      </ul>
      <Link to="/teams/new">チームを作る</Link>
      <button
        type="button"
        onClick={() => {
          auth.logout();
          navigate("/login", { replace: true });
        }}
      >
        ログアウト
      </button>
    </main>
  );
}
