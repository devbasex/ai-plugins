import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App.tsx";
import { createApi } from "./api.ts";
import { createAuth } from "./auth.ts";
import { embeddedConfig } from "./config.ts";

const config = embeddedConfig();
const auth = createAuth(config);
const api = createApi(config.apiUrl, auth);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App services={{ auth, api }} />
    </BrowserRouter>
  </StrictMode>,
);
