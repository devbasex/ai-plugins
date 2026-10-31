import { Navigate, Route, Routes } from "react-router";
import { ServicesContext, type Services } from "./context.tsx";
import { LoginPage } from "./pages/LoginPage.tsx";
import { CallbackPage } from "./pages/CallbackPage.tsx";
import { AccountPage } from "./pages/AccountPage.tsx";
import { NewTeamPage } from "./pages/NewTeamPage.tsx";
import { TeamPage } from "./pages/TeamPage.tsx";
import { RepositoriesPage } from "./pages/RepositoriesPage.tsx";
import { ModelCredentialPage } from "./pages/ModelCredentialPage.tsx";
import { NewSprintPage } from "./pages/NewSprintPage.tsx";

// ルーターは呼ぶ側が包む（main.tsx は BrowserRouter、テストは MemoryRouter）
export function App({ services }: { services: Services }) {
  return (
    <ServicesContext.Provider value={services}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/callback" element={<CallbackPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/teams/new" element={<NewTeamPage />} />
        <Route path="/accounts/:accountId" element={<TeamPage />} />
        <Route path="/accounts/:accountId/repositories" element={<RepositoriesPage />} />
        <Route path="/accounts/:accountId/model-credential" element={<ModelCredentialPage />} />
        <Route path="/accounts/:accountId/repositories/:repositoryId/sprints/new" element={<NewSprintPage />} />
        <Route path="*" element={<Navigate to="/account" replace />} />
      </Routes>
    </ServicesContext.Provider>
  );
}
