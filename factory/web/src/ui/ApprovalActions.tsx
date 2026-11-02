import { useTranslation } from "react-i18next";
import { Button } from "./Button.tsx";

// 承認する・差し戻すの 2 つのボタン。取り消せない操作なので、オフラインでは押せず「つながってから確定します」を添える
export function ApprovalActions({ online, onApprove, onReject, busy = false }: { online: boolean; onApprove: () => void; onReject: () => void; busy?: boolean }) {
  const { t } = useTranslation();
  const disabled = !online || busy;
  return (
    <div className="ui-approval">
      <Button variant="primary" disabled={disabled} onClick={onApprove}>
        {t("approval.approve")}
      </Button>
      <Button variant="danger" disabled={disabled} onClick={onReject}>
        {t("approval.reject")}
      </Button>
      {!online && <p className="ui-approval__note">{t("approval.whenOnline")}</p>}
    </div>
  );
}
