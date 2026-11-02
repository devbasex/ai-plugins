import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { AccountDetail, Me } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { ExecutionModeView, LimitsView } from "./parts.tsx";

export function AccountPage() {
  const { t } = useTranslation();
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
  if (error) return <Alert>{error}</Alert>;
  if (!me || !personal) return <LoadingState />;
  const teams = me.accounts.filter((a) => a.kind === "team");
  return (
    <>
      <PageHeader
        title={t("screens.account")}
        actions={
          <Button
            onClick={() => {
              auth.logout();
              navigate("/login", { replace: true });
            }}
          >
            {t("account.logout")}
          </Button>
        }
      />
      <p className="ui-wrap">
        {t("account.userId")} <code>{me.user_id}</code>{" "}
        <Button onClick={() => void navigator.clipboard?.writeText(me.user_id)}>{t("common.copy")}</Button>
      </p>
      <p>{t("account.idp", { idp: me.idp === "google" ? t("account.idpGoogle") : t("account.idpGithub") })}</p>
      <Section title={t("account.personal")}>
        <p>{t("account.plan", { plan: t(`plan.${personal.plan}`) })}</p>
        <ExecutionModeView account={personal} onChanged={setPersonal} />
        <LimitsView limits={personal.limits} />
        <p className="ui-links">
          <Link to={`/accounts/${personal.account_id}/repositories`}>{t("screens.repositories")}</Link>{" "}
          <Link to={`/accounts/${personal.account_id}/model-credential`}>{t("screens.modelCredential")}</Link>
        </p>
      </Section>
      <Section title={t("account.teams")}>
        <ul className="ui-plain-list">
          {teams.map((team) => (
            <li key={team.account_id}>
              <Link to={`/accounts/${team.account_id}`}>{team.name}</Link>
              {t("account.teamRole", { role: team.role === "owner" ? t("account.owner") : (team.seat_type ?? "") })}
            </li>
          ))}
        </ul>
        <Link to="/teams/new">{t("screens.newTeam")}</Link>
      </Section>
    </>
  );
}
