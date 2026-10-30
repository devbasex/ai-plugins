import { useCallback, useEffect, useState } from "react";
import { Navigate, useParams } from "react-router";
import type { AccountDetail, SeatType } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { ExecutionModeView, LimitsView, PLAN_LABELS } from "./parts.tsx";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function TeamPage() {
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const accountId = useParams().accountId ?? "";
  const [account, setAccount] = useState<AccountDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [newUser, setNewUser] = useState("");
  const [newSeatType, setNewSeatType] = useState<SeatType>("standard");
  const hasToken = auth.getToken() !== null;

  const load = useCallback(async () => {
    try {
      setAccount(await api.account(accountId));
    } catch (err) {
      setError(onFailure(err));
    }
  }, [api, accountId, onFailure]);

  useEffect(() => {
    if (hasToken) void load();
  }, [hasToken, load]);

  // 席の操作の 409・422 は操作の下に API の文を出し、一覧は読み直さない
  async function act(op: () => Promise<unknown>) {
    setMessage(null);
    try {
      await op();
      await load();
    } catch (err) {
      setMessage(onFailure(err));
    }
  }

  if (!hasToken) return <Navigate to="/login" replace />;
  if (error) return <p role="alert">{error}</p>;
  if (!account) return <p>読み込み中</p>;
  const owner = account.role === "owner";
  return (
    <main>
      <h1>{account.name}</h1>
      <p>プラン: {PLAN_LABELS[account.plan]}</p>
      <p>自分の席の種類: {account.seat_type}</p>
      <ExecutionModeView account={account} onChanged={setAccount} />
      <LimitsView limits={account.limits} />
      <h2>席</h2>
      <ul aria-label="席の一覧">
        {(account.seats ?? []).map((s) => (
          <li key={s.user_id}>
            <code>{s.user_id}</code> {s.seat_type}
            {owner && (
              <>
                <select
                  aria-label={`${s.user_id} の席の種類`}
                  value={s.seat_type}
                  onChange={(e) => void act(() => api.changeSeat(account.account_id, s.user_id, e.target.value as SeatType))}
                >
                  <option value="standard">standard</option>
                  <option value="premium">premium</option>
                </select>
                <button type="button" onClick={() => void act(() => api.removeSeat(account.account_id, s.user_id))}>
                  外す
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
      {owner && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!UUID.test(newUser)) {
              setMessage("利用者の ID の形ではありません");
              return;
            }
            void act(() => api.addSeat(account.account_id, newUser, newSeatType));
          }}
        >
          <label>
            足す利用者の ID
            <input value={newUser} maxLength={36} onChange={(e) => setNewUser(e.target.value)} />
          </label>
          <label>
            席の種類
            <select value={newSeatType} onChange={(e) => setNewSeatType(e.target.value as SeatType)}>
              <option value="standard">standard</option>
              <option value="premium">premium</option>
            </select>
          </label>
          <button type="submit">席を足す</button>
        </form>
      )}
      {message && <p role="alert">{message}</p>}
    </main>
  );
}
