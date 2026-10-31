import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router";
import type { ModelCredential } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";

const ROLE_ARN = /^arn:aws[a-z-]*:iam::\d{12}:role\/.+$/;
const REGION = /^[a-z]{2}(-[a-z]+)+-\d$/;

// キーの持ち込みの画面（#2186、形態 byok）。値は保存したら二度と出さない
export function ModelCredentialPage() {
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
  if (error) return <p role="alert">{error}</p>;
  if (!current) return <p>読み込み中</p>;

  const save = async () => {
    setMessage(null);
    if (kind === "anthropic_api_key" && (apiKey.length < 1 || apiKey.length > 512)) return setMessage("API キーを入力してください");
    if (kind === "aws_role" && (!ROLE_ARN.test(roleArn) || roleArn.length > 2048)) return setMessage("IAM のロールの ARN の形で入力してください");
    if (kind === "aws_role" && (!REGION.test(region) || region.length > 32)) return setMessage("AWS の地域の名前で入力してください");
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
    <main>
      <h1>キーの持ち込み</h1>
      <p>{current.registered ? `登録済み（${current.kind === "aws_role" ? "AWS のロール" : "Anthropic API キー"}、${current.updated_at}）` : "未登録"}</p>
      <label>
        種類
        <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          <option value="anthropic_api_key">Anthropic API キー</option>
          <option value="aws_role">AWS のロール（Bedrock）</option>
        </select>
      </label>
      {kind === "anthropic_api_key" ? (
        <label>
          API キー
          <input type="password" value={apiKey} maxLength={512} onChange={(e) => setApiKey(e.target.value)} />
        </label>
      ) : (
        <>
          <label>
            ロールの ARN
            <input value={roleArn} maxLength={2048} onChange={(e) => setRoleArn(e.target.value)} />
          </label>
          <label>
            地域
            <input value={region} maxLength={32} onChange={(e) => setRegion(e.target.value)} />
          </label>
          <p>
            外部 ID: <code>{current.external_id}</code>{" "}
            <button type="button" onClick={() => void navigator.clipboard?.writeText(current.external_id)}>
              写す
            </button>
          </p>
          <pre aria-label="信頼の設定の例">
            {JSON.stringify({ Effect: "Allow", Action: "sts:AssumeRole", Condition: { StringEquals: { "sts:ExternalId": current.external_id } } }, null, 2)}
          </pre>
        </>
      )}
      <button type="button" onClick={() => void save()}>
        保存する
      </button>
      {current.registered && (
        <button
          type="button"
          onClick={async () => {
            try {
              await api.deleteModelCredential(accountId);
              setCurrent({ ...current, registered: false, kind: null, updated_at: null });
            } catch (err) {
              setMessage(onFailure(err));
            }
          }}
        >
          消す
        </button>
      )}
      {message && <p role="alert">{message}</p>}
    </main>
  );
}
