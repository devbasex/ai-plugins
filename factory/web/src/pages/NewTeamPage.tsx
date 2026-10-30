import { useState } from "react";
import { Navigate, useNavigate } from "react-router";
import type { SeatType } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";

export function NewTeamPage() {
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [seatType, setSeatType] = useState<SeatType>("standard");
  const [message, setMessage] = useState<string | null>(null);
  if (auth.getToken() === null) return <Navigate to="/login" replace />;

  return (
    <main>
      <h1>チームを作る</h1>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const length = [...name].length;
          if (name.trim() === "" || length > 64) {
            setMessage("1〜64 字で入力してください");
            return;
          }
          try {
            const team = await api.createTeam(name, seatType);
            navigate(`/accounts/${team.account_id}`);
          } catch (err) {
            setMessage(onFailure(err));
          }
        }}
      >
        <label>
          チームの名前
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          自分の席の種類
          <select value={seatType} onChange={(e) => setSeatType(e.target.value as SeatType)}>
            <option value="standard">standard</option>
            <option value="premium">premium</option>
          </select>
        </label>
        <button type="submit">作る</button>
        {message && <p role="alert">{message}</p>}
      </form>
    </main>
  );
}
