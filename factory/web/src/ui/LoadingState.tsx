import { useTranslation } from "react-i18next";

// 読み込み中。文言を渡さなければ共通の文言を出す
export function LoadingState({ message }: { message?: string }) {
  const { t } = useTranslation();
  return (
    <p className="ui-state ui-state--loading" aria-busy="true">
      {message ?? t("common.loading")}
    </p>
  );
}
