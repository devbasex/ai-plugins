import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { AccountDetail, SeatType } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { SelectField } from "../ui/SelectField.tsx";
import { TextField } from "../ui/TextField.tsx";
import { ExecutionModeView, LimitsView, SEAT_TYPES } from "./parts.tsx";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function TeamPage() {
  const { t } = useTranslation();
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const accountId = useParams().accountId ?? "";
  const [account, setAccount] = useState<AccountDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [newUser, setNewUser] = useState("");
  const [newSeatType, setNewSeatType] = useState<SeatType>("standard");
  const hasToken = auth.getToken() !== null;

  const load = useCallback(async () => {
    try {
      setAccount(await api.account(accountId));
    } catch (err) {
      setError(onFailure(err));
    }
  }, [api, accountId, onFailure]);

  useEffect(() => {
    if (hasToken) void load();
  }, [hasToken, load]);

  // 席の操作の 409・422 は操作の下に API の文を出し、一覧は読み直さない
  async function act(op: () => Promise<unknown>) {
    setMessage(null);
    try {
      await op();
      await load();
    } catch (err) {
      setMessage(onFailure(err));
    }
  }

  if (!hasToken) return <Navigate to="/login" replace />;
  if (error) return <Alert>{error}</Alert>;
  if (!account) return <LoadingState />;
  const owner = account.role === "owner";
  const seatOptions = SEAT_TYPES.map((s) => (
    <option key={s} value={s}>
      {s}
    </option>
  ));
  return (
    <>
      <PageHeader title={account.name} />
      <p>{t("account.plan", { plan: t(`plan.${account.plan}`) })}</p>
      <p>{t("team.seatType", { seatType: account.seat_type ?? "" })}</p>
      <ExecutionModeView account={account} onChanged={setAccount} />
      <LimitsView limits={account.limits} />
      <p className="ui-links">
        <Link to={`/accounts/${account.account_id}/repositories`}>{t("screens.repositories")}</Link>
        {owner && (
          <>
            {" "}
            <Link to={`/accounts/${account.account_id}/model-credential`}>{t("screens.modelCredential")}</Link>
          </>
        )}
      </p>
      <Section title={t("team.seats")}>
        <ul aria-label={t("team.seatList")} className="ui-plain-list">
          {(account.seats ?? []).map((s) => (
            <li key={s.user_id} className="ui-row">
              <code>{s.user_id}</code> {s.seat_type}
              {owner && (
                <>
                  <SelectField
                    label={t("team.seatTypeOf", { userId: s.user_id })}
                    value={s.seat_type}
                    onChange={(e) => void act(() => api.changeSeat(account.account_id, s.user_id, e.target.value as SeatType))}
                  >
                    {seatOptions}
                  </SelectField>
                  <Button variant="danger" onClick={() => void act(() => api.removeSeat(account.account_id, s.user_id))}>
                    {t("common.remove")}
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
        {owner && (
          <form
            className="ui-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (!UUID.test(newUser)) {
                setMessage(t("team.badUserId"));
                return;
              }
              void act(() => api.addSeat(account.account_id, newUser, newSeatType));
            }}
          >
            <TextField label={t("team.newUser")} value={newUser} maxLength={36} onChange={(e) => setNewUser(e.target.value)} />
            <SelectField label={t("team.newSeatType")} value={newSeatType} onChange={(e) => setNewSeatType(e.target.value as SeatType)}>
              {seatOptions}
            </SelectField>
            <Button type="submit" variant="primary">
              {t("team.addSeat")}
            </Button>
          </form>
        )}
      </Section>
      {message && <Alert>{message}</Alert>}
    </>
  );
}
