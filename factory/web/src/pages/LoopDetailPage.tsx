import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { ApiFailure, type LoopDecision, type LoopDetail, type LoopPending } from "../api.ts";
import { useAttempt, useConnection, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { Alert } from "../ui/Alert.tsx";
import { ApprovalActions } from "../ui/ApprovalActions.tsx";
import { Button } from "../ui/Button.tsx";
import { DataList } from "../ui/DataList.tsx";
import { DetailList } from "../ui/DetailList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { StatusBadge } from "../ui/StatusBadge.tsx";
import { TextField } from "../ui/TextField.tsx";
import { BudgetFields, budgetText, parseBudget, useLoopView } from "./loopParts.tsx";
import { useSprintState } from "./parts.tsx";

type Loaded = { detail: LoopDetail | null };

// ループの詳細（#2347）。周回・実行と区間・人の判断待ちと答え・予算の変更・止める・次の周回を始める
export function LoopDetailPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const view = useLoopView();
  const sprintState = useSprintState();
  const { accountId = "", loopId = "" } = useParams();
  const { api } = useServices();
  const { online } = useConnection();
  const attempt = useAttempt();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [budget, setBudget] = useState(budgetText());
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    await attempt(async () => {
      const detail = (await api.loopDetails(accountId, [loopId]))[0] ?? null;
      setLoaded({ detail });
      if (detail) setBudget(budgetText(detail.loop.budget));
    }, setError);
  }, [api, accountId, loopId, attempt]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setMessage(null);
    setBusy(true);
    await attempt(async () => {
      await fn();
      await load();
    }, setMessage);
    setBusy(false);
  };

  // ループの行はリポジトリの ID を持たないため、戻り先はリポジトリの画面にする
  const header = (title: string) => (
    <>
      <Link to={`/accounts/${accountId}/repositories`} className="ui-back">
        {t("loopDetail.back")}
      </Link>
      <PageHeader title={title} />
    </>
  );
  if (error) {
    return (
      <>
        {header(t("screens.loopDetail"))}
        <ErrorState message={error} onRetry={() => void load()} />
      </>
    );
  }
  if (!loaded) {
    return (
      <>
        {header(t("screens.loopDetail"))}
        <LoadingState />
      </>
    );
  }
  const { detail } = loaded;
  if (!detail) {
    return (
      <>
        {header(t("screens.loopDetail"))}
        <EmptyState message={t("loopDetail.notFound")} />
      </>
    );
  }
  const { loop, laps, runs, pending } = detail;
  const state = view.state(loop.state);
  const finished = loop.state === "stopped" || loop.state === "cancelled";
  const reason = view.reason(loop);

  return (
    <>
      {header(loop.name)}
      <DetailList
        label={t("screens.loopDetail")}
        items={[
          { term: t("loops.milestones"), value: loop.milestones.join(t("loops.separator")) },
          { term: t("loops.state"), value: <StatusBadge tone={state.tone} label={state.label} /> },
          { term: t("loops.stopReason"), value: reason || t("loops.none") },
          { term: t("loops.laps"), value: format.number(loop.laps_closed) },
          { term: t("loops.cost"), value: view.usd(loop.cost_usd) },
          { term: t("loops.budgetRemaining"), value: view.remaining(loop.budget_remaining) },
          { term: t("loops.lastRunAt"), value: loop.last_run_at ? format.dateTime(loop.last_run_at) : t("loops.none") },
        ]}
      />
      {!finished && (
        <div className="ui-row">
          {loop.state === "between_laps" && loop.wait_reason === "user_start" && (
            <Button variant="primary" disabled={busy || !online} onClick={() => void act(() => api.requestNextLap(accountId, loopId))}>
              {t("loopDetail.nextLap")}
            </Button>
          )}
          <Button variant="danger" disabled={busy || !online} onClick={() => void act(() => api.cancelLoop(accountId, loopId))}>
            {t("loopDetail.cancel")}
          </Button>
        </div>
      )}
      {message && <Alert>{message}</Alert>}
      {pending.length > 0 && (
        <Section title={t("loopDetail.pending")}>
          <ul className="ui-plain-list ui-cards">
            {pending.map((p) => (
              <PendingItem
                key={p.number}
                pending={p}
                online={online}
                busy={busy}
                onAnswer={(decision, pr) => void act(() => api.answerLoop(accountId, loopId, p.number, decision, pr))}
              />
            ))}
          </ul>
        </Section>
      )}
      <Section title={t("loopDetail.runs")}>
        {runs.length === 0 ? (
          <p>{t("loops.none")}</p>
        ) : (
          <DataList
            label={t("loopDetail.runs")}
            rows={runs}
            rowKey={(r) => r.sprint_run_id}
            rowHref={(r) => `/accounts/${accountId}/sprints/${encodeURIComponent(r.sprint_run_id)}`}
            columns={[
              { key: "lap", header: t("loopDetail.lap"), render: (r) => format.number(r.lap) },
              {
                key: "state",
                header: t("loopDetail.state"),
                render: (r) => {
                  const s = sprintState(r.state);
                  const note =
                    r.state === "awaiting_reset" && r.resume_at
                      ? t("loopDetail.resumeAt", { time: format.dateTime(r.resume_at) })
                      : r.finish_reason
                        ? t(`loopDetail.finish.${r.finish_reason}`)
                        : (r.failure_reason ?? "");
                  return (
                    <>
                      <StatusBadge tone={s.tone} label={s.label} /> {note}
                    </>
                  );
                },
              },
              { key: "sections", header: t("loopDetail.sections"), render: (r) => format.number(r.sections) },
              { key: "cost", header: t("loopDetail.cost"), render: (r) => view.usd(r.cost_usd) },
            ]}
          />
        )}
      </Section>
      <Section title={t("loopDetail.laps")}>
        {laps.length === 0 ? (
          <p>{t("loops.none")}</p>
        ) : (
          <DataList
            label={t("loopDetail.laps")}
            rows={laps}
            rowKey={(l) => String(l.lap)}
            columns={[
              { key: "lap", header: t("loopDetail.lap"), render: (l) => format.number(l.lap) },
              { key: "issues", header: t("loopDetail.issues"), render: (l) => l.issues.map((n) => `#${n}`).join(" ") },
              { key: "result", header: t("loopDetail.result"), render: (l) => l.result },
              { key: "version", header: t("loopDetail.version"), render: (l) => l.version ?? t("loops.none") },
              { key: "cost", header: t("loopDetail.cost"), render: (l) => view.usd(l.cost_usd) },
              { key: "seconds", header: t("loopDetail.seconds"), render: (l) => t("loopDetail.minutes", { value: format.number(Math.round(l.seconds / 60)) }) },
            ]}
          />
        )}
      </Section>
      {!finished && (
        <Section title={t("loopDetail.budget")}>
          <form
            className="ui-form"
            onSubmit={(e) => {
              e.preventDefault();
              const parsed = parseBudget(budget);
              setBudgetError(parsed ? null : t("loops.badBudget"));
              if (!parsed) return;
              void act(async () => {
                try {
                  await api.updateLoopBudget(accountId, loopId, parsed);
                } catch (err) {
                  // 既定へ戻せない版のループで空にした（#2406）。理由は翻訳の文で予算の欄の誤りとして出す
                  if (!(err instanceof ApiFailure && err.code === "budget_reset_unsupported")) throw err;
                  setBudgetError(t("loopDetail.budgetResetUnsupported", { version: String(err.details?.ndf_version ?? "") }));
                }
              });
            }}
          >
            <BudgetFields value={budget} onChange={setBudget} error={budgetError} />
            <Button type="submit" disabled={busy || !online}>
              {t("loopDetail.saveBudget")}
            </Button>
          </form>
        </Section>
      )}
    </>
  );
}

// 人の判断待ちの課題 1 件。答えは取り消せないため、オフラインでは押せない
function PendingItem({ pending, online, busy, onAnswer }: { pending: LoopPending; online: boolean; busy: boolean; onAnswer: (d: LoopDecision, pr?: number) => void }) {
  const { t } = useTranslation();
  const [pr, setPr] = useState("");
  const [prError, setPrError] = useState<string | null>(null);
  const approve = () => {
    if (pr.trim() === "") return onAnswer("approved");
    const n = Number(pr);
    if (!Number.isInteger(n) || n < 1) return setPrError(t("loopDetail.badPr"));
    setPrError(null);
    onAnswer("approved", n);
  };
  return (
    <li className="ui-card">
      <p>{t("loopDetail.pendingIssue", { number: pending.number, lap: pending.lap, reason: pending.reason })}</p>
      {pending.answer ? (
        <p>{t("loopDetail.answered", { decision: t(`loopDetail.${pending.answer.decision}`) })}</p>
      ) : (
        <>
          <TextField label={t("loopDetail.pr")} inputMode="numeric" value={pr} onChange={(e) => setPr(e.target.value)} error={prError} />
          <ApprovalActions online={online} busy={busy} onApprove={approve} onReject={() => onAnswer("rejected")} />
        </>
      )}
    </li>
  );
}
