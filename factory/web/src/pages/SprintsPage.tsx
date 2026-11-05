import { useCallback, useState } from "react";
import { useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { SprintRunRow } from "../api.ts";
import { useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { usePolled } from "../poll.ts";
import { Button } from "../ui/Button.tsx";
import { DataList } from "../ui/DataList.tsx";
import { DetailList } from "../ui/DetailList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { SearchField } from "../ui/SearchField.tsx";
import { StatusBadge } from "../ui/StatusBadge.tsx";
import { issueNumbers, useSprintState } from "./parts.tsx";
import { PhaseBars, StaleNotice } from "./sprintParts.tsx";

type Tab = "all" | "active" | "waiting" | "finished";
const TABS: readonly Tab[] = ["all", "active", "waiting", "finished"];
const TAB_KEYS = { all: "sprints.tabAll", active: "sprints.tabActive", waiting: "sprints.tabWaiting", finished: "sprints.tabFinished" } as const;

// 動いている（starting・running・利用上限の待ち）・対応待ち（awaiting_approval）・終わった（それ以外）
const ACTIVE = new Set(["starting", "running", "awaiting_reset"]);
function tabOf(state: string): Exclude<Tab, "all"> {
  if (ACTIVE.has(state)) return "active";
  if (state === "awaiting_approval") return "waiting";
  return "finished";
}

// スプリントの一覧（#2172）。並列に流れる実行の今のフェーズと状態を出し、開いている間は読み直す
export function SprintsPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const sprintState = useSprintState();
  const { accountId = "" } = useParams();
  const { api } = useServices();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>("all");

  const read = useCallback(async () => {
    const runs = await api.sprintRuns(accountId);
    return [...runs.sprint_runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [api, accountId]);
  const { data: runs, error, staleSince, reload } = usePolled(read);

  const header = <PageHeader title={t("screens.sprints")} />;
  if (error) {
    return (
      <>
        {header}
        <ErrorState message={error} onRetry={reload} />
      </>
    );
  }
  if (!runs) {
    return (
      <>
        {header}
        <LoadingState />
      </>
    );
  }
  const needle = query.trim().replace(/^#/, "");
  const byTab = tab === "all" ? runs : runs.filter((r) => tabOf(r.state) === tab);
  const rows = needle === "" ? byTab : byTab.filter((r) => r.issues.some((n) => String(n).includes(needle)));
  return (
    <>
      {header}
      <StaleNotice since={staleSince} />
      {runs.length === 0 ? (
        <EmptyState message={t("sprints.empty")} />
      ) : (
        <>
          <DetailList
            label={t("sprints.summary")}
            items={[
              { term: t("sprints.activeCount"), value: format.number(runs.filter((r) => r.state === "starting" || r.state === "running").length) },
              { term: t("sprints.waitingCount"), value: format.number(runs.filter((r) => r.state === "awaiting_approval").length) },
            ]}
          />
          <div role="group" aria-label={t("sprints.tabs")} className="ui-row">
            {TABS.map((x) => (
              <Button key={x} variant={x === tab ? "primary" : "secondary"} aria-pressed={x === tab} onClick={() => setTab(x)}>
                {t(TAB_KEYS[x])}
              </Button>
            ))}
          </div>
          <SearchField label={t("sprints.search")} value={query} onChange={setQuery} />
          {rows.length === 0 ? (
            <EmptyState message={t("sprints.noMatch")} />
          ) : (
            <DataList<SprintRunRow>
              label={t("sprints.list")}
              rows={rows}
              rowKey={(r) => r.sprint_run_id}
              rowHref={(r) => `/accounts/${accountId}/sprints/${encodeURIComponent(r.sprint_run_id)}`}
              columns={[
                { key: "issues", header: t("sprints.issues"), render: (r) => issueNumbers(r) },
                { key: "repository", header: t("sprints.repository"), render: (r) => r.repo || t("sprints.repositoryId", { id: r.repository_id }) },
                {
                  key: "phase",
                  header: t("sprints.phase"),
                  render: (r) => (
                    <>
                      <PhaseBars current={r.current_phase} />
                      {r.current_step !== null && <span className="ui-muted">{t("sprints.currentStep", { step: r.current_step })}</span>}
                    </>
                  ),
                },
                {
                  key: "state",
                  header: t("sprints.state"),
                  render: (r) => {
                    const s = sprintState(r.state);
                    return <StatusBadge tone={s.tone} label={s.label} />;
                  },
                },
                { key: "createdAt", header: t("sprints.createdAt"), render: (r) => format.dateTime(r.created_at) },
                { key: "lastEventAt", header: t("sprints.lastEventAt"), render: (r) => (r.last_event_at === null ? t("sprints.noEvent") : format.dateTime(r.last_event_at)) },
              ]}
            />
          )}
        </>
      )}
    </>
  );
}
