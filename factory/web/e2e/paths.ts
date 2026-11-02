// ダミーデータ（src/dummy/data.ts）の ID。E2E はダミーの実装を import せず、経路だけを使う
export const PERSONAL = "00000000-0000-4000-8000-000000000001";
export const TEAM = "00000000-0000-4000-8000-000000000002";

// ログインの後の画面（既存の 6 画面と見本の 2 画面）
export const SIGNED_IN_PATHS = [
  "/account",
  "/teams/new",
  `/accounts/${TEAM}`,
  `/accounts/${PERSONAL}/repositories`,
  `/accounts/${PERSONAL}/model-credential`,
  `/accounts/${PERSONAL}/repositories/101/sprints/new`,
  `/accounts/${PERSONAL}/sprints`,
  `/accounts/${PERSONAL}/sprints/sr-0002`,
];
