import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { SprintRunSummary } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { DetailList } from "../ui/DetailList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { StatusBadge } from "../ui/StatusBadge.tsx";
import { issueNumbers, useSprintState } from "./parts.tsx";
import { repositoryNames } from "./SprintsPage.tsx";

type Loaded = { run: SprintRunSummary | null; repository: string | null };

// スプリントの詳細（見本の画面）。中身は #2172 が置き換える
export function SprintDetailPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const sprintState = useSprintState();
  const { accountId = "", sprintRunId = "" } = useParams();
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasToken = auth.getToken() !== null;

  const load = useCallback(async () => {
    setError(null);
    setLoaded(null);
    try {
      const [runs, names] = await Promise.all([api.sprintRunDetails(accountId, [sprintRunId]), repositoryNames(() => api.repositories(accountId))]);
      const run = runs[0] ?? null;
      setLoaded({ run, repository: run ? (names.get(run.repository_id) ?? null) : null });
    } catch (err) {
      setError(onFailure(err));
    }
  }, [api, accountId, sprintRunId, onFailure]);

  useEffect(() => {
    if (hasToken) void load();
  }, [hasToken, load]);

  if (!hasToken) return <Navigate to="/login" replace />;
  const back = (
    <Link to={`/accounts/${accountId}/sprints`} className="ui-back">
      {t("sprintDetail.back")}
    </Link>
  );
  const header = (title: string) => (
    <>
      {back}
      <PageHeader title={title} />
    </>
  );
  if (error) {
    return (
      <>
        {header(t("screens.sprintDetail"))}
        <ErrorState message={error} onRetry={() => void load()} />
      </>
    );
  }
  if (!loaded) {
    return (
      <>
        {header(t("screens.sprintDetail"))}
        <LoadingState />
      </>
    );
  }
  const { run, repository } = loaded;
  if (!run) {
    return (
      <>
        {header(t("screens.sprintDetail"))}
        <EmptyState message={t("sprintDetail.notFound")} />
      </>
    );
  }
  const state = sprintState(run.state);
  return (
    <>
      {header(issueNumbers(run))}
      <DetailList
        label={t("screens.sprintDetail")}
        items={[
          { term: t("sprints.issues"), value: issueNumbers(run) },
          { term: t("sprints.repository"), value: repository ?? t("sprints.repositoryId", { id: run.repository_id }) },
          { term: t("sprints.state"), value: <StatusBadge tone={state.tone} label={state.label} /> },
          { term: t("sprintDetail.failureReason"), value: run.failure_reason ?? t("sprintDetail.none") },
          { term: t("sprints.createdAt"), value: format.dateTime(run.created_at) },
        ]}
      />
    </>
  );
}
