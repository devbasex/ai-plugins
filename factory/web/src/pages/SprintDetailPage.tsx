import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { isFinishedRun, type SprintRunDetail, type StepDetail, type StepRow } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { usePolled } from "../poll.ts";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { DetailList } from "../ui/DetailList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { StatusBadge } from "../ui/StatusBadge.tsx";
import { issueNumbers, useSprintState } from "./parts.tsx";
import { PhaseBars, StaleNotice, usePhaseLabel } from "./sprintParts.tsx";

const REQUIRED_KEYS = { retro: "sprintDetail.requiredStep.retro", refine: "sprintDetail.requiredStep.refine" } as const;
// 文言を持つ注意の理由。ほかの理由は値そのものを出す（#2430 の決定 6）
const ATTENTION_REASON_KEYS = { base_conflict: "sprintDetail.attentionReason.base_conflict" } as const;

// 費用は USD（usage.recorded の cost_usd）。金額は通貨とともに整形する
const usd = (amount: number) => ({ amount, currency: "USD" });

// 承認の欄（設計承認ゲート・リリース承認ゲート・判断の 3 つ。承認の欄は 2 つだけ）
function GatesView({ gates }: { gates: SprintRunDetail["gates"] }) {
  const { t } = useTranslation();
  return (
    <Section title={t("sprintDetail.gates")}>
      <DetailList
        items={[
          { term: t("sprintDetail.gate1"), value: t(`sprintDetail.gateState.${gates.gate1.state}`) },
          { term: t("sprintDetail.gate2"), value: t(`sprintDetail.gateState.${gates.gate2.state}`) },
          { term: t("sprintDetail.decision"), value: t(`sprintDetail.decisionState.${gates.decision.state}`) },
        ]}
      />
    </Section>
  );
}

// 注意の欄（#2430）。詳細の応答の attention を記録された順に出す
function AttentionView({ attention }: { attention: SprintRunDetail["attention"] }) {
  const { t } = useTranslation();
  const format = useFormat();
  const reasonLabel = (reason: string) => (Object.hasOwn(ATTENTION_REASON_KEYS, reason) ? t(ATTENTION_REASON_KEYS[reason as keyof typeof ATTENTION_REASON_KEYS]) : reason);
  return (
    <Section title={t("sprintDetail.attention")}>
      {attention.length === 0 ? (
        <p>{t("sprintDetail.attentionEmpty")}</p>
      ) : (
        <ul className="ui-plain-list">
          {attention.map((a) => (
            <li key={a.key}>
              <strong>{reasonLabel(a.reason)}</strong> <span className="ui-wrap">{a.summary}</span> <span className="ui-muted">{format.dateTime(a.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// 右側の既定: 承認ゲート・Pull Request と CI・AI が代わりに判断した件数・使用量
function OverviewPanel({ detail }: { detail: SprintRunDetail }) {
  const { t } = useTranslation();
  const format = useFormat();
  const number = format.number;
  const prs = detail.columns.flatMap((c) => c.pull_requests.map((pr) => ({ ...pr, issue: c.issue })));
  return (
    <>
      <GatesView gates={detail.gates} />
      <Section title={t("sprintDetail.pullRequests")}>
        {prs.length === 0 ? (
          <p>{t("sprintDetail.pullRequestsEmpty")}</p>
        ) : (
          <ul className="ui-plain-list">
            {prs.map((pr) => (
              <li key={`${pr.issue}-${pr.repo}-${pr.number}`}>
                <a href={pr.url} target="_blank" rel="noreferrer">
                  {t("sprintDetail.prLabel", { number: pr.number })}
                </a>{" "}
                <span>{pr.issue === null ? t("sprintDetail.columnSprint") : t("sprintDetail.columnIssue", { issue: pr.issue })}</span>{" "}
                <StatusBadge tone={pr.state === "merged" ? "success" : pr.state === "unknown" ? "neutral" : "info"} label={t(`sprintDetail.prState.${pr.state}`)} />
                <ul className="ui-plain-list">
                  {pr.checks.map((c) => (
                    <li key={c.name}>{t("sprintDetail.checkLine", { name: c.name, conclusion: c.conclusion ?? t("sprintDetail.checkRunning") })}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title={t("sprintDetail.usage")}>
        <DetailList
          items={[
            { term: t("sprintDetail.delegated"), value: number(detail.delegated_decisions) },
            { term: t("sprintDetail.inputTokens"), value: number(detail.usage.input_tokens) },
            { term: t("sprintDetail.outputTokens"), value: number(detail.usage.output_tokens) },
            { term: t("sprintDetail.cost"), value: format.money(usd(detail.usage.cost_usd)) },
          ]}
        />
      </Section>
    </>
  );
}

// 工程で変更したファイル。null は記録なし、空は 0 件
function StepFiles({ files, omitted }: { files: StepDetail["files"]; omitted: StepDetail["files_omitted"] }) {
  const { t } = useTranslation();
  return files === null ? (
    <p>{t("sprintDetail.noRecord")}</p>
  ) : files.length === 0 ? (
    <p>{t("sprintDetail.noFiles")}</p>
  ) : (
    <ul className="ui-plain-list">
      {files.map((f) => (
        <li key={f.path}>
          {f.url ? (
            <a href={f.url} target="_blank" rel="noreferrer" className="ui-wrap">
              {f.path}
            </a>
          ) : (
            <span className="ui-wrap">{f.path}</span>
          )}{" "}
          <span>{f.added === null || f.deleted === null ? t("sprintDetail.binary") : t("sprintDetail.fileChange", { added: f.added, deleted: f.deleted })}</span>
        </li>
      ))}
      {omitted ? <li>{t("sprintDetail.filesOmitted", { count: omitted })}</li> : null}
    </ul>
  );
}

// 工程で下した判断。null は記録なし、空は 0 件
function StepDecisions({ decisions }: { decisions: StepDetail["decisions"] }) {
  const { t } = useTranslation();
  return decisions === null ? (
    <p>{t("sprintDetail.noRecord")}</p>
  ) : decisions.length === 0 ? (
    <p>{t("sprintDetail.noDecisions")}</p>
  ) : (
    <ul className="ui-plain-list">
      {decisions.map((d, i) => (
        <li key={i}>
          {t("sprintDetail.decisionLine", { question: d.question, answer: d.answer })} <StatusBadge tone="neutral" label={t(`sprintDetail.decidedBy.${d.by}`)} />
        </li>
      ))}
    </ul>
  );
}

// 工程の詳細パネル。開いたときに 1 件だけ読む（読み直さない）
function StepPanel({ accountId, sprintRunId, stepKey, onBack }: { accountId: string; sprintRunId: string; stepKey: string; onBack: () => void }) {
  const { t } = useTranslation();
  const format = useFormat();
  const phaseLabel = usePhaseLabel();
  const { api } = useServices();
  const onFailure = useFailureHandler();
  const [step, setStep] = useState<StepDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showOutput, setShowOutput] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setStep(null);
    setError(null);
    setShowOutput(false);
    api
      .stepDetails(accountId, sprintRunId, [stepKey])
      .then((found) => {
        if (!cancelled) setStep(found[0] ?? null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(onFailure(err));
      });
    return () => {
      cancelled = true;
    };
  }, [api, accountId, sprintRunId, stepKey, onFailure]);

  const back = (
    <Button onClick={onBack} className="sprint-panel__back">
      {t("sprintDetail.backToOverview")}
    </Button>
  );
  const noRecord = t("sprintDetail.noRecord");
  return (
    <Section title={t("sprintDetail.panel")}>
      {back}
      {error ? (
        <Alert>{error}</Alert>
      ) : !step ? (
        <LoadingState />
      ) : (
        <>
          <DetailList
            items={[
              { term: t("sprintDetail.stepName"), value: t("sprintDetail.stepLine", { step: step.step, phase: phaseLabel(step.phase) }) },
              { term: t("sprintDetail.exitCode"), value: String(step.exit_code) },
              { term: t("sprintDetail.seconds"), value: format.number(step.seconds) },
              { term: t("sprintDetail.stepCost"), value: step.cost_usd === null ? t("sprintDetail.noCost") : format.money(usd(step.cost_usd)) },
              { term: t("sprintDetail.summaryLabel"), value: step.summary },
            ]}
          />
          <h3>{t("sprintDetail.files")}</h3>
          <StepFiles files={step.files} omitted={step.files_omitted} />
          <h3>{t("sprintDetail.decisions")}</h3>
          <StepDecisions decisions={step.decisions} />
          <div className="ui-row">
            {step.diff_url && (
              <a href={step.diff_url} target="_blank" rel="noreferrer">
                {t("sprintDetail.diff")}
              </a>
            )}
            {step.output ? (
              <Button aria-expanded={showOutput} onClick={() => setShowOutput((v) => !v)}>
                {showOutput ? t("sprintDetail.closeOutput") : step.output.kind === "log" ? t("sprintDetail.openLog") : t("sprintDetail.openReport")}
              </Button>
            ) : (
              <span>{noRecord}</span>
            )}
          </div>
          {step.output && showOutput && (
            <>
              {step.output.truncated && <p>{t("sprintDetail.truncated")}</p>}
              <pre className="sprint-panel__output">{step.output.text}</pre>
            </>
          )}
        </>
      )}
    </Section>
  );
}

// 工程の記録（新しいものが上）。課題の欄ごとに絞り、押すと右が詳細パネルになる
function StepLog({ detail, selected, onSelect }: { detail: SprintRunDetail; selected: string | null; onSelect: (key: string) => void }) {
  const { t } = useTranslation();
  const format = useFormat();
  const phaseLabel = usePhaseLabel();
  const [column, setColumn] = useState<number | null | "all">("all");
  const keys = column === "all" ? null : new Set(detail.columns.find((c) => c.issue === column)?.step_keys ?? []);
  const rows: StepRow[] = [...detail.steps].reverse().filter((s) => keys === null || keys.has(s.key));
  const columnLabel = (issue: number | null) => (issue === null ? t("sprintDetail.columnSprint") : t("sprintDetail.columnIssue", { issue }));
  return (
    <Section title={t("sprintDetail.steps")}>
      <div role="group" aria-label={t("sprintDetail.columns")} className="ui-row">
        <Button variant={column === "all" ? "primary" : "secondary"} aria-pressed={column === "all"} onClick={() => setColumn("all")}>
          {t("sprintDetail.columnAll")}
        </Button>
        {detail.columns.map((c) => (
          <Button key={String(c.issue)} variant={column === c.issue ? "primary" : "secondary"} aria-pressed={column === c.issue} onClick={() => setColumn(c.issue)}>
            {columnLabel(c.issue)}
          </Button>
        ))}
      </div>
      {detail.omitted_steps > 0 && <p>{t("sprintDetail.omitted", { count: detail.omitted_steps })}</p>}
      {rows.length === 0 ? (
        <EmptyState message={t("sprintDetail.stepsEmpty")} />
      ) : (
        <ul className="ui-plain-list sprint-steps">
          {rows.map((s) => (
            <li key={s.key}>
              <button type="button" className={`sprint-steps__row${s.key === selected ? " sprint-steps__row--selected" : ""}`} aria-pressed={s.key === selected} onClick={() => onSelect(s.key)}>
                <strong>{t("sprintDetail.stepLine", { step: s.step, phase: phaseLabel(s.phase) })}</strong>
                <span className="ui-muted">{t("sprintDetail.stepMeta", { code: s.exit_code, seconds: format.number(s.seconds), at: format.dateTime(s.at) })}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// フェーズの欄。仕上げには振り返りと棚卸しの行を常に置く
function PhasesView({ detail }: { detail: SprintRunDetail }) {
  const { t } = useTranslation();
  const label = usePhaseLabel();
  return (
    <Section title={t("sprintDetail.phases")}>
      <PhaseBars current={detail.current_phase} />
      <ul className="ui-plain-list sprint-phases">
        {detail.phases.map((p) => (
          <li key={p.name} className="ui-card">
            <strong>{label(p.name)}</strong> <span>{t("sprintDetail.phaseSteps", { count: p.step_keys.length })}</span>
            {p.required.length > 0 && (
              <DetailList
                label={t("sprintDetail.required")}
                items={p.required.map((r) => ({
                  term: r.step in REQUIRED_KEYS ? t(REQUIRED_KEYS[r.step as keyof typeof REQUIRED_KEYS]) : r.label,
                  value: r.record === null ? t("sprintDetail.notYet") : t("sprintDetail.recordResult", { code: r.record.exit_code, seconds: r.record.seconds }),
                }))}
              />
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

// スプリントの詳細（#2172）。開いている間は読み直し（終わった実行は除く）、工程を押すと右が詳細パネルに切り替わる
export function SprintDetailPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const sprintState = useSprintState();
  const { accountId = "", sprintRunId = "" } = useParams();
  const { api } = useServices();
  const [selected, setSelected] = useState<string | null>(null);

  const read = useCallback(async () => (await api.sprintRunDetails(accountId, [sprintRunId]))[0] ?? null, [api, accountId, sprintRunId]);
  // 終わった実行は変わらないため、間隔では読み直さない（開いたときと手動の読み直しだけ。1 回の読み出しが事象の全件を読む）
  const { data: detail, error, staleSince, reload } = usePolled(read, { poll: (d) => d === null || !isFinishedRun(d.state) });

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
        <ErrorState message={error} onRetry={reload} />
      </>
    );
  }
  if (detail === null) {
    return (
      <>
        {header(t("screens.sprintDetail"))}
        <LoadingState />
      </>
    );
  }
  const state = sprintState(detail.state);
  return (
    <>
      {header(issueNumbers(detail))}
      <StaleNotice since={staleSince} />
      <div className="ui-row">
        <StatusBadge tone={state.tone} label={state.label} /> <span className="ui-wrap">{detail.repo}</span>{" "}
        <a href={`https://github.com/${detail.repo}`} target="_blank" rel="noreferrer">
          {t("sprintDetail.openGitHub")}
        </a>
      </div>
      <Section title={t("sprintDetail.overview")}>
        <DetailList
          items={[
            { term: t("sprintDetail.ndfVersion"), value: detail.ndf_version },
            { term: t("sprintDetail.executionMode"), value: t(`mode.${detail.execution_mode}`) },
            { term: t("sprints.createdAt"), value: format.dateTime(detail.created_at) },
            { term: t("sprintDetail.jobCount"), value: format.number(detail.job_count) },
            { term: t("sprintDetail.failureReason"), value: detail.failure_reason ?? t("sprintDetail.none") },
          ]}
        />
      </Section>
      <AttentionView attention={detail.attention} />
      <PhasesView detail={detail} />
      <div className="sprint-detail">
        <div className="sprint-detail__log">
          <StepLog detail={detail} selected={selected} onSelect={setSelected} />
        </div>
        <div className="sprint-detail__side">
          {selected === null ? (
            <OverviewPanel detail={detail} />
          ) : (
            <StepPanel accountId={accountId} sprintRunId={detail.sprint_run_id} stepKey={selected} onBack={() => setSelected(null)} />
          )}
        </div>
      </div>
    </>
  );
}
