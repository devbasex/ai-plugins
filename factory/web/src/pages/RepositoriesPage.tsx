import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router";
import type { AccountDetail, Repository, RepositoryCandidate, SprintRunSummary } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";

const STATE_LABELS: Record<string, string> = {
  starting: "起動中",
  running: "動いている",
  awaiting_approval: "承認待ち",
  done: "終わった",
  stopped: "止まった",
  failed: "失敗",
  cancelled: "取り消した",
};

// リポジトリの画面（#2186）。導入の案内・GitHub で確かめる・候補から登録・一覧と外す・実行の状態
export function RepositoriesPage() {
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
    const byRepo: Record<number, SprintRunSummary[]> = {};
    for (const r of list) byRepo[r.repository_id] = (await api.sprintRuns(accountId, r.repository_id)).sprint_runs;
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
  if (error) return <p role="alert">{error}</p>;
  if (!account || !repos) return <p>読み込み中</p>;
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
    <main>
      <h1>リポジトリ</h1>
      {installUrl && (
        <p>
          <a href={installUrl}>GitHub App をリポジトリへ入れる</a>
        </p>
      )}
      {owner && (
        <button type="button" onClick={() => void act(async () => window.location.assign((await api.authorizeUrl(accountId)).url))}>
          GitHub で確かめる
        </button>
      )}
      {candidates && (
        <section aria-label="登録の候補">
          <h2>登録の候補</h2>
          <ul>
            {candidates.list.map((c) => (
              <li key={c.repository_id}>
                {c.full_name}{" "}
                {c.registered ? (
                  "（登録済み）"
                ) : (
                  <button type="button" onClick={() => void act(() => api.registerRepository(accountId, candidates.proof, c.repository_id))}>
                    登録する
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {repos.length === 0 ? (
        <p>まだリポジトリがありません</p>
      ) : (
        <ul aria-label="登録したリポジトリ">
          {repos.map((r) => (
            <li key={r.repository_id}>
              <strong>{r.full_name}</strong>{" "}
              <Link to={`/accounts/${accountId}/repositories/${r.repository_id}/sprints/new`}>スプリントを始める</Link>{" "}
              {owner && (
                <button type="button" onClick={() => void act(() => api.removeRepository(accountId, r.repository_id))}>
                  外す
                </button>
              )}
              <ul>
                {(runs[r.repository_id] ?? []).map((s) => (
                  <li key={s.sprint_run_id}>
                    {s.issues.map((n) => `#${n}`).join(" ")}: {STATE_LABELS[s.state] ?? s.state}
                    {s.failure_reason ? `（${s.failure_reason}）` : ""}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
      {message && <p role="alert">{message}</p>}
    </main>
  );
}
