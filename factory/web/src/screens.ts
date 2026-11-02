// 画面の地図の定義。経路の定義（App.tsx）とナビゲーション（ui/NavMenu.tsx）はここから作り、経路を別に書かない
// 人が読む地図（役割ごとの動線・確定仕様の機能との対応）は docs/specifications/ndf-factory-screen-map.md にある

export type Role = "personal" | "team_owner" | "team_seat";

export const ALL_ROLES: readonly Role[] = ["personal", "team_owner", "team_seat"];

export type ScreenId =
  | "login"
  | "callback"
  | "account"
  | "newTeam"
  | "team"
  | "repositories"
  | "modelCredential"
  | "newSprint"
  | "sprints"
  | "sprintDetail"
  | "backlog"
  | "approvals"
  | "billing"
  | "runners"
  | "loops";

export interface Screen {
  id: ScreenId;
  // 経路が未定の予定の画面は null
  path: string | null;
  // 空ならログインの前の画面（枠で包まない）
  roles: readonly Role[];
  nav: boolean;
  status: "available" | "planned";
}

const OWNERS: readonly Role[] = ["personal", "team_owner"];

export const SCREENS: readonly Screen[] = [
  { id: "login", path: "/login", roles: [], nav: false, status: "available" },
  { id: "callback", path: "/callback", roles: [], nav: false, status: "available" },
  { id: "account", path: "/account", roles: ALL_ROLES, nav: true, status: "available" },
  { id: "newTeam", path: "/teams/new", roles: ["personal"], nav: false, status: "available" },
  { id: "team", path: "/accounts/:accountId", roles: ["team_owner", "team_seat"], nav: false, status: "available" },
  { id: "sprints", path: "/accounts/:accountId/sprints", roles: ALL_ROLES, nav: true, status: "available" },
  { id: "sprintDetail", path: "/accounts/:accountId/sprints/:sprintRunId", roles: ALL_ROLES, nav: false, status: "available" },
  { id: "repositories", path: "/accounts/:accountId/repositories", roles: ALL_ROLES, nav: true, status: "available" },
  { id: "modelCredential", path: "/accounts/:accountId/model-credential", roles: OWNERS, nav: true, status: "available" },
  { id: "newSprint", path: "/accounts/:accountId/repositories/:repositoryId/sprints/new", roles: ALL_ROLES, nav: false, status: "available" },
  { id: "backlog", path: "/accounts/:accountId/repositories/:repositoryId/backlog", roles: ALL_ROLES, nav: false, status: "planned" },
  { id: "approvals", path: null, roles: ALL_ROLES, nav: false, status: "planned" },
  { id: "billing", path: null, roles: OWNERS, nav: false, status: "planned" },
  { id: "runners", path: null, roles: OWNERS, nav: false, status: "planned" },
  { id: "loops", path: null, roles: ALL_ROLES, nav: false, status: "planned" },
];

export type AvailableScreen = Screen & { path: string };

export function availableScreens(): AvailableScreen[] {
  return SCREENS.filter((s): s is AvailableScreen => s.status === "available" && s.path !== null);
}

// 地図に無い経路を開いたときの行き先
export const FALLBACK_PATH = "/account";
