import { useSearchParams } from "react-router";
import { useServices } from "../context.tsx";

export function LoginPage() {
  const { auth } = useServices();
  const [params] = useSearchParams();
  return (
    <main>
      <h1>Neural Development Factory にログイン</h1>
      {params.get("error") !== null && <p role="alert">ログインできませんでした</p>}
      <button type="button" onClick={() => void auth.login("Google")}>
        Google でログイン
      </button>
      <button type="button" onClick={() => void auth.login("GitHub")}>
        GitHub でログイン
      </button>
    </main>
  );
}
