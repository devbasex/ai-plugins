import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { ApiFailure, type LoopRow } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { DataList } from "../ui/DataList.tsx";
import { EmptyState } from "../ui/EmptyState.tsx";
import { ErrorState } from "../ui/ErrorState.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { StatusBadge } from "../ui/StatusBadge.tsx";
import { TextField } from "../ui/TextField.tsx";
import { BudgetFields, budgetText, parseBudget, useLoopView } from "./loopParts.tsx";

const NAME = /^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$/;
const MAX_MILESTONES = 10;

// ループの画面（#2347）。リポジトリのループの一覧と、始める欄
export function LoopsPage() {
  const { t } = useTranslation();
  const format = useFormat();
  const view = useLoopView();
  const navigate = useNavigate();
  const { accountId = "", repositoryId = "" } = useParams();
  const repoId = Number(repositoryId);
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const [loops, setLoops] = useState<LoopRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [milestones, setMilestones] = useState("");
  const [budget, setBudget] = useState(budgetText());
  const [fieldErrors, setFieldErrors] = useState<{ name?: string; milestones?: string; budget?: string }>({});
  const [message, setMessage] = useState<string | null>(null);
  const hasToken = auth.getToken() !== null;

  const load = useCallback(async () => {
    setError(null);
    setLoops(null);
    try {
      setLoops((await api.loops(accountId, repoId)).loops);
    } catch (err) {
      setError(onFailure(err));
    }
  }, [api, accountId, repoId, onFailure]);

  useEffect(() => {
    if (hasToken) void load();
  }, [hasToken, load]);

  if (!hasToken) return <Navigate to="/login" replace />;

  const start = async () => {
    setMessage(null);
    const titles = [...new Set(milestones.split("\n").map((m) => m.trim()).filter((m) => m !== ""))];
    const parsed = parseBudget(budget);
    const errors = {
      ...(NAME.test(name) ? {} : { name: t("loops.badName") }),
      ...(titles.length === 0 ? { milestones: t("loops.needMilestone") } : titles.length > MAX_MILESTONES ? { milestones: t("loops.tooManyMilestones") } : {}),
      ...(parsed ? {} : { budget: t("loops.badBudget") }),
    };
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0 || !parsed) return;
    try {
      const out = await api.startLoop(accountId, repoId, { name, milestones: titles, budget: parsed });
      navigate(`/accounts/${accountId}/loops/${encodeURIComponent(out.loop_id)}`);
    } catch (err) {
      if (err instanceof ApiFailure && err.code === "milestone_in_use") {
        const taken = (err.details?.milestones as string[] | undefined) ?? [];
        setMessage(t("loops.milestoneInUse", { milestones: taken.join(t("loops.separator")), loop: String(err.details?.loop_name ?? "") }));
      } else if (err instanceof ApiFailure && err.code === "loop_name_in_use") {
        setMessage(t("loops.nameInUse"));
      } else {
        setMessage(onFailure(err));
      }
    }
  };

  const header = (
    <>
      <Link to={`/accounts/${accountId}/repositories`} className="ui-back">
        {t("loops.back")}
      </Link>
      <PageHeader title={t("screens.loops")} />
    </>
  );
  if (error) {
    return (
      <>
        {header}
        <ErrorState message={error} onRetry={() => void load()} />
      </>
    );
  }
  if (!loops) {
    return (
      <>
        {header}
        <LoadingState />
      </>
    );
  }

  return (
    <>
      {header}
      {loops.length === 0 ? (
        <EmptyState message={t("loops.empty")} />
      ) : (
        <DataList
          label={t("loops.list")}
          rows={loops}
          rowKey={(l) => l.loop_id}
          rowHref={(l) => `/accounts/${accountId}/loops/${encodeURIComponent(l.loop_id)}`}
          columns={[
            { key: "name", header: t("loops.name"), render: (l) => l.name },
            { key: "milestones", header: t("loops.milestones"), render: (l) => l.milestones.join(t("loops.separator")) },
            {
              key: "state",
              header: t("loops.state"),
              render: (l) => {
                const s = view.state(l.state);
                return <StatusBadge tone={s.tone} label={s.label} />;
              },
            },
            { key: "laps", header: t("loops.laps"), render: (l) => format.number(l.laps_closed) },
            { key: "cost", header: t("loops.cost"), render: (l) => view.usd(l.cost_usd) },
            { key: "remaining", header: t("loops.budgetRemaining"), render: (l) => view.remaining(l.budget_remaining) },
            { key: "last", header: t("loops.lastRunAt"), render: (l) => (l.last_run_at ? format.dateTime(l.last_run_at) : t("loops.none")) },
            { key: "reason", header: t("loops.stopReason"), render: (l) => view.reason(l) || t("loops.none") },
          ]}
        />
      )}
      <Section title={t("loops.start")}>
        <form
          className="ui-form"
          onSubmit={(e) => {
            e.preventDefault();
            void start();
          }}
        >
          <TextField label={t("loops.name")} value={name} onChange={(e) => setName(e.target.value)} error={fieldErrors.name} maxLength={64} />
          <label className="ui-field">
            <span className="ui-field__label">{t("loops.milestones")}</span>
            <textarea
              className="ui-field__control"
              rows={3}
              value={milestones}
              placeholder={t("loops.milestonesHint")}
              aria-invalid={fieldErrors.milestones ? true : undefined}
              onChange={(e) => setMilestones(e.target.value)}
            />
            {fieldErrors.milestones && <span className="ui-field__error">{fieldErrors.milestones}</span>}
          </label>
          <BudgetFields value={budget} onChange={setBudget} error={fieldErrors.budget ?? null} />
          <Button type="submit" variant="primary">
            {t("loops.start")}
          </Button>
        </form>
      </Section>
      {message && <Alert>{message}</Alert>}
    </>
  );
}
