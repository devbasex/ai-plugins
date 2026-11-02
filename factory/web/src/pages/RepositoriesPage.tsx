import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { AccountDetail, Repository, RepositoryCandidate, SprintRunSummary } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { issueNumbers, useSprintState } from "./parts.tsx";

// リポジトリの画面（#2186）。導入の案内・GitHub で確かめる・候補から登録・一覧と外す・実行の状態
export function RepositoriesPage() {
  const { t } = useTranslation();
  const sprintState = useSprintState();
  const { accountId = "" } = useParams();
  const [params] = useSearchParams();
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const [account, setAccount] = useState<AccountDetail | null>(null);
  const [repos, setRepos] = useState<Repository[] | null>(null);
  const [runs, setRuns] = useState<Record<number, SprintRunSummary[]>>({});
  const [installUrl, setInstallUrl] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<{ proof: string; list: RepositoryCandidate[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const hasToken = auth.getToken() !== null;

  const reload = useCallback(async () => {
    const list = (await api.repositories(accountId)).repositories;
    setRepos(list);
    // 実行の一覧は 1 回だけ取り、リポジトリごとに分ける（リポジトリの数だけ API を呼ばない）
    const byRepo: Record<number, SprintRunSummary[]> = Object.fromEntries(list.map((r) => [r.repository_id, [] as SprintRunSummary[]]));
    if (list.length > 0) for (const run of (await api.sprintRuns(accountId)).sprint_runs) byRepo[run.repository_id]?.push(run);
    setRuns(byRepo);
  }, [api, accountId]);

  useEffect(() => {
    if (!hasToken) return;
    void (async () => {
      try {
        setAccount(await api.account(accountId));
        setInstallUrl((await api.installUrl(accountId)).url);
        await reload();
        const code = params.get("code");
        const state = params.get("state");
        if (code && state) {
          const out = await api.repositoryCandidates(accountId, code, state);
          setCandidates({ proof: out.proof, list: out.candidates });
        }
      } catch (err) {
        setError(onFailure(err));
      }
    })();
  }, [api, accountId, hasToken, onFailure, params, reload]);

  if (!hasToken) return <Navigate to="/login" replace />;
  if (error) return <Alert>{error}</Alert>;
  if (!account || !repos) return <LoadingState />;
  const owner = account.role === "owner";
  const act = async (fn: () => Promise<unknown>) => {
    setMessage(null);
    try {
      await fn();
      await reload();
    } catch (err) {
      setMessage(onFailure(err));
    }
  };

  return (
    <>
      <PageHeader
        title={t("screens.repositories")}
        actions={
          owner && (
            <Button onClick={() => void act(async () => window.location.assign((await api.authorizeUrl(accountId)).url))}>{t("repositories.verify")}</Button>
          )
        }
      />
      {installUrl && (
        <p>
          <a href={installUrl}>{t("repositories.install")}</a>
        </p>
      )}
      {candidates && (
        <Section title={t("repositories.candidates")}>
          <ul className="ui-plain-list">
            {candidates.list.map((c) => (
              <li key={c.repository_id} className="ui-row">
                {c.full_name}{" "}
                {c.registered ? (
                  t("repositories.registered")
                ) : (
                  <Button onClick={() => void act(() => api.registerRepository(accountId, candidates.proof, c.repository_id))}>{t("repositories.register")}</Button>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {repos.length === 0 ? (
        <EmptyState message={t("repositories.empty")} />
      ) : (
        <ul aria-label={t("repositories.list")} className="ui-plain-list ui-cards">
          {repos.map((r) => (
            <li key={r.repository_id} className="ui-card">
              <div className="ui-row">
                <strong className="ui-wrap">{r.full_name}</strong>{" "}
                <Link to={`/accounts/${accountId}/repositories/${r.repository_id}/sprints/new`}>{t("screens.newSprint")}</Link>{" "}
                {owner && (
                  <Button variant="danger" onClick={() => void act(() => api.removeRepository(accountId, r.repository_id))}>
                    {t("common.remove")}
                  </Button>
                )}
              </div>
              <ul className="ui-plain-list">
                {(runs[r.repository_id] ?? []).map((s) => {
                  const values = { issues: issueNumbers(s), state: sprintState(s.state).label, reason: s.failure_reason ?? "" };
                  return (
                    <li key={s.sprint_run_id}>
                      <Link to={`/accounts/${accountId}/sprints/${encodeURIComponent(s.sprint_run_id)}`}>
                        {s.failure_reason ? t("repositories.runWithReason", values) : t("repositories.run", values)}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      )}
      {message && <Alert>{message}</Alert>}
    </>
  );
}
