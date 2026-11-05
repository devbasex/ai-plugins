import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { Delegation, DelegationScope } from "../api.ts";
import { useConnection, useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { DataList } from "../ui/DataList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { SelectField } from "../ui/SelectField.tsx";
import { StatusBadge, type StatusTone } from "../ui/StatusBadge.tsx";
import { TextField } from "../ui/TextField.tsx";

const MAX_NAME = 64;
// 期限の選択肢（秒）。API は 300〜3,600 秒を受ける
const EXPIRES = [900, 1800, 3600] as const;
const TONE: Record<Delegation["state"], StatusTone> = { active: "success", expired: "neutral", revoked: "warning" };
const STATE_KEY = { active: "delegations.stateActive", expired: "delegations.stateExpired", revoked: "delegations.stateRevoked" } as const;

// エージェントへの委任の画面（#2384）。値は発行の直後に 1 度だけ出し、閉じると二度と出さない。
// 発行と取り消しは人だけが確定でき、オフラインの間は確定させない
export function DelegationsPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const { auth, api, apiUrl } = useServices();
  const { online } = useConnection();
  const onFailure = useFailureHandler();
  const [rows, setRows] = useState<Delegation[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<DelegationScope>("read");
  const [expires, setExpires] = useState<(typeof EXPIRES)[number]>(3600);
  const [issued, setIssued] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hasToken = auth.getToken() !== null;

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setRows(await api.delegations());
    } catch (err) {
      setLoadError(onFailure(err));
    }
  }, [api, onFailure]);

  useEffect(() => {
    if (hasToken) void load();
  }, [hasToken, load]);

  if (!hasToken) return <Navigate to="/login" replace />;

  const issue = async () => {
    setMessage(null);
    if (name.trim() === "") return setMessage(t("delegations.needClientName"));
    setBusy(true);
    try {
      const { delegation, credential } = await api.createDelegation({ client_name: name, scope, expires_in_seconds: expires });
      setIssued(credential);
      setName("");
      // 結果が分からない失敗の後の送り直しは、同じ鍵で前の発行を返し、値を返さない（値は発行の 1 度だけ）。
      // 黙って受けると、使えない有効な資格情報が残るため、取り消して発行し直すよう知らせる
      if (credential === null) setMessage(t("delegations.valueUnavailable"));
      setRows((prev) => [delegation, ...(prev ?? []).filter((d) => d.credential_id !== delegation.credential_id)]);
    } catch (err) {
      setMessage(onFailure(err));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (credentialId: string) => {
    setMessage(null);
    setBusy(true);
    try {
      const next = await api.revokeDelegation(credentialId);
      setRows((prev) => (prev ?? []).map((d) => (d.credential_id === credentialId ? next : d)));
    } catch (err) {
      setMessage(onFailure(err));
    } finally {
      setBusy(false);
    }
  };

  const example = issued && JSON.stringify({ url: `${apiUrl ?? ""}/mcp`, headers: { Authorization: `Bearer ${issued}` } }, null, 2);

  return (
    <>
      <PageHeader title={t("screens.delegations")} />
      <p>{t("delegations.intro")}</p>
      {issued && (
        <Section title={t("delegations.issuedTitle")}>
          <Alert>{t("delegations.issuedNote")}</Alert>
          <p className="ui-wrap">
            {t("delegations.value")} <code>{issued}</code>{" "}
            <Button onClick={() => void navigator.clipboard?.writeText(issued)}>{t("common.copy")}</Button>
          </p>
          <pre aria-label={t("delegations.registerExample")} className="ui-code">
            {example}
          </pre>
          <Button onClick={() => setIssued(null)}>{t("delegations.close")}</Button>
        </Section>
      )}
      <div className="ui-form">
        <TextField label={t("delegations.clientName")} value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)} />
        <SelectField label={t("delegations.scope")} value={scope} onChange={(e) => setScope(e.target.value as DelegationScope)}>
          <option value="read">{t("delegations.scopeRead")}</option>
          <option value="read_write">{t("delegations.scopeReadWrite")}</option>
        </SelectField>
        <SelectField label={t("delegations.expires")} value={String(expires)} onChange={(e) => setExpires(Number(e.target.value) as (typeof EXPIRES)[number])}>
          {EXPIRES.map((s) => (
            <option key={s} value={s}>
              {s < 3600 ? t("delegations.minutes", { count: s / 60 }) : t("delegations.hours", { count: s / 3600 })}
            </option>
          ))}
        </SelectField>
        <div className="ui-row">
          <Button variant="primary" disabled={busy || !online} onClick={() => void issue()}>
            {t("delegations.issue")}
          </Button>
        </div>
      </div>
      {!online && <p>{t("delegations.offline")}</p>}
      {message && <Alert>{message}</Alert>}
      <Section title={t("delegations.list")}>
        {loadError ? (
          <ErrorState message={loadError} onRetry={() => void load()} />
        ) : !rows ? (
          <LoadingState />
        ) : rows.length === 0 ? (
          <EmptyState message={t("delegations.empty")} />
        ) : (
          <DataList
            label={t("delegations.list")}
            rows={rows}
            rowKey={(d) => d.credential_id}
            columns={[
              { key: "name", header: t("delegations.clientName"), render: (d) => d.client_name },
              { key: "scope", header: t("delegations.scope"), render: (d) => (d.scope === "read" ? t("delegations.scopeRead") : t("delegations.scopeReadWrite")) },
              { key: "created", header: t("delegations.createdAt"), render: (d) => format.dateTime(d.created_at) },
              { key: "expires", header: t("delegations.expiresAt"), render: (d) => format.dateTime(d.expires_at) },
              { key: "state", header: t("delegations.state"), render: (d) => <StatusBadge tone={TONE[d.state]} label={t(STATE_KEY[d.state])} /> },
              {
                key: "revoke",
                header: "",
                render: (d) =>
                  d.state === "active" ? (
                    <Button variant="danger" disabled={busy || !online} onClick={() => void revoke(d.credential_id)}>
                      {t("delegations.revoke")}
                    </Button>
                  ) : null,
              },
            ]}
          />
        )}
      </Section>
    </>
  );
}
