import { useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { SeatType } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { SelectField } from "../ui/SelectField.tsx";
import { TextField } from "../ui/TextField.tsx";
import { SEAT_TYPES } from "./parts.tsx";

export function NewTeamPage() {
  const { t } = useTranslation();
  const { auth, api } = useServices();
  const onFailure = useFailureHandler();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [seatType, setSeatType] = useState<SeatType>("standard");
  const [message, setMessage] = useState<string | null>(null);
  if (auth.getToken() === null) return <Navigate to="/login" replace />;

  return (
    <>
      <PageHeader title={t("screens.newTeam")} />
      <form
        className="ui-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const length = [...name].length;
          if (name.trim() === "" || length > 64) {
            setMessage(t("newTeam.nameLength"));
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
        <TextField label={t("newTeam.name")} value={name} onChange={(e) => setName(e.target.value)} />
        <SelectField label={t("newTeam.seatType")} value={seatType} onChange={(e) => setSeatType(e.target.value as SeatType)}>
          {SEAT_TYPES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </SelectField>
        <Button type="submit" variant="primary">
          {t("newTeam.create")}
        </Button>
        {message && <Alert>{message}</Alert>}
      </form>
    </>
  );
}
