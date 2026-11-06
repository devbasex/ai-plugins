// 版を上げる前のプランが持つ「フェーズ」の旧い値を新しい値へ読み替える（#2620 の決定 5）。
// Factory はサービスの境界の規則で plugins/ を読まないため、NDF の読み替えの表の phase の集合をここに写して持つ
const LEGACY_PHASES: Readonly<Record<string, string>> = {
  "配布（開発版）": "開発版のデプロイ",
  "配布（本番）": "リリース",
  配布の依頼: "リリースの依頼",
  "配布（提示）": "リリース（提示）",
  "配布（昇格）": "リリース（昇格）",
  "配布（承認ゲート 2）": "リリース（承認ゲート 2）",
  "配布（導入の確認）": "デプロイ（導入の確認）",
};

export function normalizePhase(value: string): string {
  return Object.hasOwn(LEGACY_PHASES, value) ? LEGACY_PHASES[value]! : value;
}
