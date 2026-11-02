import { useTranslation } from "react-i18next";
import { Button } from "./Button.tsx";

// 読み込みの失敗。文言とやり直しのボタン。画面を白くしない
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="ui-state ui-state--error">
      <p role="alert">{message}</p>
      {onRetry && <Button onClick={onRetry}>{t("common.retry")}</Button>}
    </div>
  );
}
