import { useCallback, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import type { Backlog } from "../api.ts";
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
import { Section } from "../ui/Section.tsx";

type Refinement = Backlog["refinements"][number];
type RankRow = NonNullable<Backlog["ranking"]>["rows"][number];

// バックログの画面（#2172）。順位の表と棚卸しの記録をスプリントの外で読む。読むだけで、課題・マイルストーン・ボードへ書く操作を持たない
export function BacklogPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const { accountId = "", repositoryId = "" } = useParams();
  const { api } = useServices();
  const read = useCallback(async () => (await api.backlogs(accountId, [Number(repositoryId)]))[0] ?? null, [api, accountId, repositoryId]);
  // 1 回の読み出しが GitHub の全ページと実行ごとの事象を読むため、間隔では読み直さない（開いたときと「もう一度読む」だけ）
  const { data: backlog, error, reload } = usePolled(read, { poll: false });

  const header = (title: string, actions?: ReactNode) => (
    <>
      <Link to={`/accounts/${accountId}/repositories`} className="ui-back">
        {t("backlog.back")}
      </Link>
      <PageHeader title={title} actions={actions} />
    </>
  );
  if (error) {
    return (
      <>
        {header(t("screens.backlog"))}
        <ErrorState message={error} onRetry={reload} />
      </>
    );
  }
  if (backlog === null) {
    return (
      <>
        {header(t("screens.backlog"))}
        <LoadingState />
      </>
    );
  }
  const last = backlog.refinements[0];
  return (
    <>
      {header(backlog.repo, <Button onClick={reload}>{t("common.retry")}</Button>)}
      <DetailList
        items={[
          { term: t("backlog.lastRefinement"), value: last ? format.dateTime(last.at) : t("backlog.none") },
          { term: t("backlog.milestone"), value: backlog.ranking?.milestone ?? t("backlog.none") },
        ]}
      />
      <Section title={t("backlog.ranking")}>
        {backlog.ranking === null ? (
          <EmptyState message={t("backlog.rankingEmpty")} />
        ) : (
          <DataList<RankRow>
            label={t("backlog.ranking")}
            rows={backlog.ranking.rows}
            rowKey={(r) => String(r.number)}
            columns={[
              { key: "rank", header: t("backlog.rank"), render: (r) => format.number(r.rank) },
              { key: "issue", header: t("backlog.issue"), render: (r) => t("backlog.issueLine", { number: r.number, title: r.title }) },
            ]}
          />
        )}
      </Section>
      <Section title={t("backlog.refinements")}>
        {backlog.refinements.length === 0 ? (
          <EmptyState message={t("backlog.refinementsEmpty")} />
        ) : (
          <DataList<Refinement>
            label={t("backlog.refinements")}
            rows={backlog.refinements}
            rowKey={(r) => `${r.sprint_run_id}-${r.key}`}
            rowHref={(r) => `/accounts/${accountId}/sprints/${encodeURIComponent(r.sprint_run_id)}`}
            columns={[
              { key: "at", header: t("backlog.at"), render: (r) => format.dateTime(r.at) },
              { key: "summary", header: t("backlog.summary"), render: (r) => r.summary },
              {
                key: "pr",
                header: t("backlog.pullRequest"),
                render: (r) =>
                  r.pull_request ? (
                    <a href={r.pull_request.url} target="_blank" rel="noreferrer">
                      {t("sprintDetail.prLabel", { number: r.pull_request.number })}
                    </a>
                  ) : (
                    t("backlog.none")
                  ),
              },
              { key: "exit", header: t("backlog.exitCode"), render: (r) => String(r.exit_code) },
            ]}
          />
        )}
      </Section>
    </>
  );
}
