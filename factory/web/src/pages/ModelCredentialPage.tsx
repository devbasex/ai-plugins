import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { ModelCredential } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { SelectField } from "../ui/SelectField.tsx";
import { TextField } from "../ui/TextField.tsx";

const ROLE_ARN = /^arn:aws[a-z-]*:iam::\d{12}:role\/.+$/;
const REGION = /^[a-z]{2}(-[a-z]+)+-\d$/;

// キーの持ち込みの画面（#2186、形態 byok）。値は保存したら二度と出さない
export function ModelCredentialPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const { accountId = "" } = useParams();
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const [current, setCurrent] = useState<ModelCredential | null>(null);
  const [kind, setKind] = useState<"anthropic_api_key" | "aws_role">("anthropic_api_key");
  const [apiKey, setApiKey] = useState("");
  const [roleArn, setRoleArn] = useState("");
  const [region, setRegion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const hasToken = auth.getToken() !== null;

  useEffect(() => {
    if (!hasToken) return;
    void (async () => {
      try {
        setCurrent(await api.modelCredential(accountId));
      } catch (err) {
        setError(onFailure(err));
      }
    })();
  }, [api, accountId, hasToken, onFailure]);

  if (!hasToken) return <Navigate to="/login" replace />;
  if (error) return <Alert>{error}</Alert>;
  if (!current) return <LoadingState />;

  const save = async () => {
    setMessage(null);
    if (kind === "anthropic_api_key" && (apiKey.length < 1 || apiKey.length > 512)) return setMessage(t("modelCredential.needApiKey"));
    if (kind === "aws_role" && (!ROLE_ARN.test(roleArn) || roleArn.length > 2048)) return setMessage(t("modelCredential.badRoleArn"));
    if (kind === "aws_role" && (!REGION.test(region) || region.length > 32)) return setMessage(t("modelCredential.badRegion"));
    try {
      setCurrent(
        await api.saveModelCredential(accountId, kind === "anthropic_api_key" ? { kind, api_key: apiKey } : { kind, role_arn: roleArn, region }),
      );
      setApiKey("");
    } catch (err) {
      setMessage(onFailure(err));
    }
  };

  return (
    <>
      <PageHeader title={t("screens.modelCredential")} />
      <p>
        {current.registered
          ? t("modelCredential.registered", {
              kind: current.kind === "aws_role" ? t("modelCredential.awsShort") : t("modelCredential.anthropic"),
              updatedAt: current.updated_at === null ? "" : format.dateTime(current.updated_at),
            })
          : t("modelCredential.unregistered")}
      </p>
      <div className="ui-form">
        <SelectField label={t("modelCredential.kind")} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          <option value="anthropic_api_key">{t("modelCredential.anthropic")}</option>
          <option value="aws_role">{t("modelCredential.aws")}</option>
        </SelectField>
        {kind === "anthropic_api_key" ? (
          <TextField label={t("modelCredential.apiKey")} type="password" value={apiKey} maxLength={512} onChange={(e) => setApiKey(e.target.value)} />
        ) : (
          <>
            <TextField label={t("modelCredential.roleArn")} value={roleArn} maxLength={2048} onChange={(e) => setRoleArn(e.target.value)} />
            <TextField label={t("modelCredential.region")} value={region} maxLength={32} onChange={(e) => setRegion(e.target.value)} />
            <p className="ui-wrap">
              {t("modelCredential.externalId")} <code>{current.external_id}</code>{" "}
              <Button onClick={() => void navigator.clipboard?.writeText(current.external_id)}>{t("common.copy")}</Button>
            </p>
            <pre aria-label={t("modelCredential.trustExample")} className="ui-code">
              {JSON.stringify({ Effect: "Allow", Action: "sts:AssumeRole", Condition: { StringEquals: { "sts:ExternalId": current.external_id } } }, null, 2)}
            </pre>
          </>
        )}
        <div className="ui-row">
          <Button variant="primary" onClick={() => void save()}>
            {t("modelCredential.save")}
          </Button>
          {current.registered && (
            <Button
              variant="danger"
              onClick={async () => {
                try {
                  await api.deleteModelCredential(accountId);
                  setCurrent({ ...current, registered: false, kind: null, updated_at: null });
                } catch (err) {
                  setMessage(onFailure(err));
                }
              }}
            >
              {t("modelCredential.delete")}
            </Button>
          )}
        </div>
      </div>
      {message && <Alert>{message}</Alert>}
    </>
  );
}
