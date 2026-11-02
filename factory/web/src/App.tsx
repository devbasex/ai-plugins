import { useMemo, type ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router";
import { I18nextProvider } from "react-i18next";
import { ServicesContext, type Services } from "./context.tsx";
import { browserConnection, type ConnectionSource } from "./connection.ts";
import { bundledResources, createI18n, type TranslationResources } from "./i18n/index.ts";
import { LocaleSettingsContext, detectSettings, type LocaleSettings } from "./i18n/settings.ts";
import { FALLBACK_PATH, availableScreens, type ScreenId } from "./screens.ts";
import { AppShell } from "./ui/AppShell.tsx";
import { LoginPage } from "./pages/LoginPage.tsx";
import { CallbackPage } from "./pages/CallbackPage.tsx";
import { AccountPage } from "./pages/AccountPage.tsx";
import { NewTeamPage } from "./pages/NewTeamPage.tsx";
import { TeamPage } from "./pages/TeamPage.tsx";
import { RepositoriesPage } from "./pages/RepositoriesPage.tsx";
import { ModelCredentialPage } from "./pages/ModelCredentialPage.tsx";
import { NewSprintPage } from "./pages/NewSprintPage.tsx";
import { SprintsPage } from "./pages/SprintsPage.tsx";
import { SprintDetailPage } from "./pages/SprintDetailPage.tsx";

// 地図の available の行の画面。経路は screens.ts だけが持つ
const ELEMENTS: Partial<Record<ScreenId, ReactElement>> = {
  login: <LoginPage />,
  callback: <CallbackPage />,
  account: <AccountPage />,
  newTeam: <NewTeamPage />,
  team: <TeamPage />,
  repositories: <RepositoriesPage />,
  modelCredential: <ModelCredentialPage />,
  newSprint: <NewSprintPage />,
  sprints: <SprintsPage />,
  sprintDetail: <SprintDetailPage />,
};

export interface AppProps {
  // connection が無いときはブラウザのつながりを使う
  services: Omit<Services, "connection"> & { connection?: ConnectionSource };
  // 無いときはブラウザから決める
  settings?: LocaleSettings;
  // 足す言語の翻訳のファイル（無いときは同梱の翻訳のファイルだけ）
  resources?: TranslationResources;
}

// ルーターは呼ぶ側が包む（main.tsx は BrowserRouter、テストは MemoryRouter）
// ログインの前の画面（/login・/callback）と地図に無い経路の転送は枠で包まない
export function App({ services, settings, resources }: AppProps) {
  const full = useMemo<Services>(() => ({ ...services, connection: services.connection ?? browserConnection() }), [services]);
  const allResources = useMemo(() => ({ ...bundledResources, ...resources }), [resources]);
  const locale = useMemo(() => settings ?? detectSettings(Object.keys(allResources)), [settings, allResources]);
  const i18n = useMemo(() => createI18n(locale.language, allResources), [locale.language, allResources]);
  const screens = availableScreens();
  const route = (s: (typeof screens)[number]) => <Route key={s.id} path={s.path} element={ELEMENTS[s.id]} />;
  return (
    <I18nextProvider i18n={i18n}>
      <LocaleSettingsContext.Provider value={locale}>
        <ServicesContext.Provider value={full}>
          <Routes>
            {screens.filter((s) => s.roles.length === 0).map(route)}
            <Route element={<AppShell />}>{screens.filter((s) => s.roles.length > 0).map(route)}</Route>
            <Route path="*" element={<Navigate to={FALLBACK_PATH} replace />} />
          </Routes>
        </ServicesContext.Provider>
      </LocaleSettingsContext.Provider>
    </I18nextProvider>
  );
}
