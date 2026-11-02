import { useCallback, useEffect, useState } from "react";
import { Navigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { Repository, SprintRunSummary } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { DataList } from "../ui/DataList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { SearchField } from "../ui/SearchField.tsx";
import { StatusBadge } from "../ui/StatusBadge.tsx";
import { issueNumbers, useSprintState } from "./parts.tsx";

type Loaded = { runs: SprintRunSummary[]; repositories: Map<number, string> };

// リポジトリの ID から名前を引く表。名前が読めなくても一覧は出す
export async function repositoryNames(list: () => Promise<{ repositories: Repository[] }>): Promise<Map<number, string>> {
  try {
    return new Map((await list()).repositories.map((r) => [r.repository_id, r.full_name]));
  } catch {
    return new Map();
  }
}

// スプリントの一覧（見本の画面）。中身は #2172 が置き換える
export function SprintsPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const sprintState = useSprintState();
  const { accountId = "" } = useParams();
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const hasToken = auth.getToken() !== null;

  const load = useCallback(async () => {
    setError(null);
    setLoaded(null);
    try {
      const [runs, repositories] = await Promise.all([api.sprintRuns(accountId), repositoryNames(() => api.repositories(accountId))]);
      const sorted = [...runs.sprint_runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
      setLoaded({ runs: sorted, repositories });
    } catch (err) {
      setError(onFailure(err));
    }
  }, [api, accountId, onFailure]);

  useEffect(() => {
    if (hasToken) void load();
  }, [hasToken, load]);

  if (!hasToken) return <Navigate to="/login" replace />;
  const header = <PageHeader title={t("screens.sprints")} />;
  if (error) {
    return (
      <>
        {header}
        <ErrorState message={error} onRetry={() => void load()} />
      </>
    );
  }
  if (!loaded) {
    return (
      <>
        {header}
        <LoadingState />
      </>
    );
  }
  const needle = query.trim().replace(/^#/, "");
  const rows = needle === "" ? loaded.runs : loaded.runs.filter((r) => r.issues.some((n) => String(n).includes(needle)));
  return (
    <>
      {header}
      {loaded.runs.length === 0 ? (
        <EmptyState message={t("sprints.empty")} />
      ) : (
        <>
          <SearchField label={t("sprints.search")} value={query} onChange={setQuery} />
          {rows.length === 0 ? (
            <EmptyState message={t("sprints.noMatch")} />
          ) : (
            <DataList
              label={t("sprints.list")}
              rows={rows}
              rowKey={(r) => r.sprint_run_id}
              rowHref={(r) => `/accounts/${accountId}/sprints/${encodeURIComponent(r.sprint_run_id)}`}
              columns={[
                { key: "issues", header: t("sprints.issues"), render: (r) => issueNumbers(r) },
                {
                  key: "state",
                  header: t("sprints.state"),
                  render: (r) => {
                    const s = sprintState(r.state);
                    return <StatusBadge tone={s.tone} label={s.label} />;
                  },
                },
                {
                  key: "repository",
                  header: t("sprints.repository"),
                  render: (r) => loaded.repositories.get(r.repository_id) ?? t("sprints.repositoryId", { id: r.repository_id }),
                },
                { key: "createdAt", header: t("sprints.createdAt"), render: (r) => format.dateTime(r.created_at) },
              ]}
            />
          )}
        </>
      )}
    </>
  );
}
