import { useTranslation } from "react-i18next";
import type { ConnectionState } from "../connection.ts";
import { useFormat } from "../i18n/index.ts";

// つながりの状態の 3 つの見せ方: オンライン・オフライン（最後に受け取った時刻）・つながってから確定する操作がある
export function ConnectionStatus({ state }: { state: ConnectionState }) {
  const { t } = useTranslation();
  const format = useFormat();
  const main = state.online
    ? t("connection.online")
    : state.lastReceivedAt
      ? t("connection.offlineSince", { time: format.dateTime(state.lastReceivedAt) })
      : t("connection.offline");
  return (
    <div role="status" aria-label={t("connection.label")} className={`ui-connection ${state.online ? "ui-connection--online" : "ui-connection--offline"}`}>
      <span className="ui-connection__main">{main}</span>
      {state.pendingOperations > 0 && <span className="ui-connection__pending">{t("connection.pending", { count: state.pendingOperations })}</span>}
    </div>
  );
}
