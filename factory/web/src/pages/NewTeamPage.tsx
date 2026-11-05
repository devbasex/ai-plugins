import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { SeatType } from "../api.ts";
import { useAttempt, useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";
import { SelectField } from "../ui/SelectField.tsx";
import { TextField } from "../ui/TextField.tsx";
import { SeatTypeOptions } from "./parts.tsx";

export function NewTeamPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const attempt = useAttempt();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [seatType, setSeatType] = useState<SeatType>("standard");
  const [message, setMessage] = useState<string | null>(null);

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
          await attempt(async () => {
            const team = await api.createTeam(name, seatType);
            navigate(`/accounts/${team.account_id}`);
          }, setMessage);
        }}
      >
        <TextField label={t("newTeam.name")} value={name} onChange={(e) => setName(e.target.value)} />
        <SelectField label={t("newTeam.seatType")} value={seatType} onChange={(e) => setSeatType(e.target.value as SeatType)}>
          <SeatTypeOptions />
        </SelectField>
        <Button type="submit" variant="primary">
          {t("newTeam.create")}
        </Button>
        {message && <Alert>{message}</Alert>}
      </form>
    </>
  );
}
