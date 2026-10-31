import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router";
import { ApiFailure, type IssueCandidate } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";

const MAX_ISSUES = 20;

interface Reason {
  quantity: string;
  limit: number | null;
  current: number | null;
}

// スプリントを始める画面（#2186）。AI が選んだ候補と理由・外す・番号で足す・AI に選び直させる・開始
export function NewSprintPage() {
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
  if (error) return <p role="alert">{error}</p>;
  if (!candidates) return <p>読み込み中</p>;
  const titles = new Map(candidates.map((c) => [c.number, c]));

  const start = async () => {
    setMessage(null);
    try {
      await api.startSprintRun(accountId, repoId, selected);
      navigate(`/accounts/${accountId}/repositories`);
    } catch (err) {
      if (err instanceof ApiFailure && err.code === "limits_exceeded") {
        const r = ((err.details?.reasons as Reason[] | undefined) ?? []).find((x) => x.quantity === "concurrent_sprint_runs");
        setMessage(r ? `同時に動かせるスプリントは ${r.limit} 本までです（今 ${r.current} 本）` : err.message);
      } else if (err instanceof ApiFailure && err.code === "invalid_issues") {
        const numbers = (err.details?.numbers as number[] | undefined) ?? [];
        setMessage(`${numbers.map((n) => `#${n}`).join(" ")} は開いている課題ではありません`);
      } else {
        setMessage(onFailure(err));
      }
    }
  };

  return (
    <main>
      <h1>スプリントを始める</h1>
      {note && <p>{note}</p>}
      <ul aria-label="選んだ課題">
        {selected.map((n) => (
          <li key={n}>
            #{n} {titles.get(n)?.title ?? ""} <small>{titles.get(n)?.reason ?? "手で足した"}</small>{" "}
            <button type="button" onClick={() => setSelected(selected.filter((x) => x !== n))}>
              外す
            </button>
          </li>
        ))}
      </ul>
      <h2>ほかの候補</h2>
      <ul>
        {candidates
          .filter((c) => !selected.includes(c.number))
          .map((c) => (
            <li key={c.number}>
              #{c.number} {c.title} <small>{c.reason}</small>{" "}
              <button type="button" disabled={selected.length >= MAX_ISSUES} onClick={() => setSelected([...selected, c.number])}>
                足す
              </button>
            </li>
          ))}
      </ul>
      <label>
        番号で足す
        <input inputMode="numeric" value={extra} onChange={(e) => setExtra(e.target.value)} />
      </label>
      <button
        type="button"
        onClick={() => {
          const n = Number(extra);
          if (Number.isInteger(n) && n >= 1 && !selected.includes(n) && selected.length < MAX_ISSUES) setSelected([...selected, n]);
          setExtra("");
        }}
      >
        足す
      </button>
      <button type="button" onClick={() => void choose(candidates.filter((c) => !selected.includes(c.number)).map((c) => c.number)).catch((err: unknown) => setMessage(onFailure(err)))}>
        AI に選び直させる
      </button>
      <button type="button" disabled={selected.length === 0} onClick={() => void start()}>
        開始する
      </button>
      {message && <p role="alert">{message}</p>}
    </main>
  );
}
