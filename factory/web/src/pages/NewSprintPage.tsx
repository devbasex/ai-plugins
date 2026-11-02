import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { ApiFailure, type IssueCandidate } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { LoadingState } from "../ui/LoadingState.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { Section } from "../ui/Section.tsx";
import { TextField } from "../ui/TextField.tsx";

const MAX_ISSUES = 20;

interface Reason {
  quantity: string;
  limit: number | null;
  current: number | null;
}

// スプリントを始める画面（#2186）。AI が選んだ候補と理由・外す・番号で足す・AI に選び直させる・開始
export function NewSprintPage() {
  const { t } = useTranslation();
  const { accountId = "", repositoryId = "" } = useParams();
  const repoId = Number(repositoryId);
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const navigate = useNavigate();
  const [candidates, setCandidates] = useState<IssueCandidate[] | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [extra, setExtra] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const hasToken = auth.getToken() !== null;

  const choose = useCallback(
    async (exclude: number[]) => {
      const out = await api.issueCandidates(accountId, repoId, exclude);
      setCandidates(out.candidates);
      setSelected(out.candidates.filter((c) => c.selected).map((c) => c.number));
      setNote(out.note);
    },
    [api, accountId, repoId],
  );

  useEffect(() => {
    if (!hasToken) return;
    choose([]).catch((err: unknown) => setError(onFailure(err)));
  }, [choose, hasToken, onFailure]);

  if (!hasToken) return <Navigate to="/login" replace />;
  if (error) return <Alert>{error}</Alert>;
  if (!candidates) return <LoadingState />;
  const titles = new Map(candidates.map((c) => [c.number, c]));

  const start = async () => {
    setMessage(null);
    try {
      await api.startSprintRun(accountId, repoId, selected);
      navigate(`/accounts/${accountId}/repositories`);
    } catch (err) {
      if (err instanceof ApiFailure && err.code === "limits_exceeded") {
        const r = ((err.details?.reasons as Reason[] | undefined) ?? []).find((x) => x.quantity === "concurrent_sprint_runs");
        setMessage(r ? t("newSprint.concurrentLimit", { limit: r.limit, current: r.current }) : err.message);
      } else if (err instanceof ApiFailure && err.code === "invalid_issues") {
        const numbers = (err.details?.numbers as number[] | undefined) ?? [];
        setMessage(t("newSprint.invalidIssues", { numbers: numbers.map((n) => `#${n}`).join(" ") }));
      } else {
        setMessage(onFailure(err));
      }
    }
  };

  return (
    <>
      <PageHeader title={t("screens.newSprint")} />
      {note && <p>{note}</p>}
      <ul aria-label={t("newSprint.selected")} className="ui-plain-list">
        {selected.map((n) => (
          <li key={n} className="ui-row">
            #{n} {titles.get(n)?.title ?? ""} <small>{titles.get(n)?.reason ?? t("newSprint.manual")}</small>{" "}
            <Button onClick={() => setSelected(selected.filter((x) => x !== n))}>{t("common.remove")}</Button>
          </li>
        ))}
      </ul>
      <Section title={t("newSprint.others")}>
        <ul className="ui-plain-list">
          {candidates
            .filter((c) => !selected.includes(c.number))
            .map((c) => (
              <li key={c.number} className="ui-row">
                #{c.number} {c.title} <small>{c.reason}</small>{" "}
                <Button disabled={selected.length >= MAX_ISSUES} onClick={() => setSelected([...selected, c.number])}>
                  {t("common.add")}
                </Button>
              </li>
            ))}
        </ul>
      </Section>
      <div className="ui-row">
        <TextField label={t("newSprint.addByNumber")} inputMode="numeric" value={extra} onChange={(e) => setExtra(e.target.value)} />
        <Button
          onClick={() => {
            const n = Number(extra);
            if (Number.isInteger(n) && n >= 1 && !selected.includes(n) && selected.length < MAX_ISSUES) setSelected([...selected, n]);
            setExtra("");
          }}
        >
          {t("common.add")}
        </Button>
      </div>
      <div className="ui-row">
        <Button
          onClick={() => void choose(candidates.filter((c) => !selected.includes(c.number)).map((c) => c.number)).catch((err: unknown) => setMessage(onFailure(err)))}
        >
          {t("newSprint.rechoose")}
        </Button>
        <Button variant="primary" disabled={selected.length === 0} onClick={() => void start()}>
          {t("newSprint.start")}
        </Button>
      </div>
      {message && <Alert>{message}</Alert>}
    </>
  );
}
