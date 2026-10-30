import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router";
import { useServices } from "../context.tsx";

// ログインの戻り。成功なら /account、IdP の拒否・取り消しは token を保存せず /login?error=… へ
export function CallbackPage() {
  const { auth } = useServices();
  const navigate = useNavigate();
  const location = useLocation();
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const url = new URL(`${location.pathname}${location.search}`, window.location.origin).toString();
    void auth.completeLogin(url).then((result) => {
      if (result.ok) navigate("/account", { replace: true });
      else navigate(`/login?error=${encodeURIComponent(result.error)}`, { replace: true });
    });
  }, [auth, navigate, location]);
  return <p>読み込み中</p>;
}
