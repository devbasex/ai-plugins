import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useServices } from "../context.tsx";
import { Alert } from "../ui/Alert.tsx";
import { Button } from "../ui/Button.tsx";
import { PageHeader } from "../ui/PageHeader.tsx";

export function LoginPage() {
  const { t } = useTranslation();
  const { auth } = useServices();
  const [params] = useSearchParams();
  return (
    <main className="ui-shell__main ui-login">
      <PageHeader title={t("login.title")} />
      {params.get("error") !== null && <Alert>{t("login.failed")}</Alert>}
      <div className="ui-stack">
        <Button variant="primary" onClick={() => void auth.login("Google")}>
          {t("login.google")}
        </Button>
        <Button variant="primary" onClick={() => void auth.login("GitHub")}>
          {t("login.github")}
        </Button>
      </div>
    </main>
  );
}
