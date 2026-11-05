import { useCallback } from "react";
import { useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { ApprovalRequestRow } from "../api.ts";
import { useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { usePolled } from "../poll.ts";
import { DataList } from "../ui/DataList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { issueNumbers } from "./parts.tsx";
import { StaleNotice } from "./sprintParts.tsx";

// 種類の文言はスプリントの詳細の承認の欄と同じ
const KIND_KEYS = { gate1: "sprintDetail.gate1", gate2: "sprintDetail.gate2", decision: "sprintDetail.decision" } as const;

// 承認待ちの一覧（#2430）。アカウントで答えを待っている承認の依頼を、ループの名前と周回の番号とともに古い順に出す。
// 読むだけで、承認と差し戻しのボタンを置かない。開いている間は読み直す
export function ApprovalsPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const { accountId = "" } = useParams();
  const { api } = useServices();

  // 並びは API が決める（待ち始めた日時の古い順）
  const read = useCallback(() => api.approvalRequests(accountId), [api, accountId]);
  const { data: rows, error, staleSince, reload } = usePolled(read);

  const header = <PageHeader title={t("screens.approvals")} />;
  if (error) {
    return (
      <>
        {header}
        <ErrorState message={error} onRetry={reload} />
      </>
    );
  }
  if (!rows) {
    return (
      <>
        {header}
        <LoadingState />
      </>
    );
  }
  const noLoop = t("approvals.noLoop");
  return (
    <>
      {header}
      <StaleNotice since={staleSince} />
      {rows.length === 0 ? (
        <EmptyState message={t("approvals.empty")} />
      ) : (
        <DataList<ApprovalRequestRow>
          label={t("approvals.list")}
          rows={rows}
          rowKey={(r) => r.sprint_run_id}
          rowHref={(r) => `/accounts/${accountId}/sprints/${encodeURIComponent(r.sprint_run_id)}`}
          columns={[
            { key: "kind", header: t("approvals.kind"), render: (r) => t(KIND_KEYS[r.kind]) },
            { key: "loop", header: t("approvals.loop"), render: (r) => r.loop?.name ?? noLoop },
            { key: "lap", header: t("approvals.lap"), render: (r) => (r.loop ? format.number(r.loop.lap) : noLoop) },
            { key: "repository", header: t("approvals.repository"), render: (r) => <span className="ui-wrap">{r.repo}</span> },
            { key: "issues", header: t("approvals.issues"), render: (r) => issueNumbers(r) },
            { key: "waitingSince", header: t("approvals.waitingSince"), render: (r) => format.dateTime(r.waiting_since) },
          ]}
        />
      )}
    </>
  );
}
