import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App.tsx";
import { createApi } from "./api.ts";
import { createAuth } from "./auth.ts";
import { embeddedConfig } from "./config.ts";
import { browserConnection } from "./connection.ts";
import type { Services } from "./context.tsx";
import tokens from "./ui/tokens.json";
import { tokensToCss } from "./ui/tokens.ts";
import "./ui/ui.css";

// ダミーと本物の切り替えはここ 1 か所。mode が dummy のときだけダミーを動的に読む
// 本番のビルドでは MODE が "production" に置き換わり、ダミーの分岐ごと成果物から落ちる（check:bundle が確かめる）
async function buildServices(): Promise<Services> {
  if (import.meta.env.MODE === "dummy") {
    const { createDummyServices } = await import("./dummy/index.ts");
    return createDummyServices(window.location.search);
  }
  const config = embeddedConfig();
  const auth = createAuth(config);
  return { auth, api: createApi(config.apiUrl, auth), connection: browserConnection() };
}

const style = document.createElement("style");
style.textContent = tokensToCss(tokens);
document.head.append(style);

void buildServices().then((services) =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <BrowserRouter>
        <App services={services} />
      </BrowserRouter>
    </StrictMode>,
  ),
);
