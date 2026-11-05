// フェーズの名前（#2172）。phase.changed と step.recorded の phase が運ぶ 5 つの名前の正はここだけにある。
// 定義（schemas/）では文字列のままにし、列挙で縛らない（NDF の版がフェーズを足しても事象を 400 にしない）。
// 画面も読むため、この入口（@ndf-factory/contracts/phases）は依存を持たない
export const PHASE_NAMES = ["設計", "実装", "検査", "取り込み", "仕上げ"] as const;

export type PhaseName = (typeof PHASE_NAMES)[number];

export function isPhaseName(value: unknown): value is PhaseName {
  return typeof value === "string" && (PHASE_NAMES as readonly string[]).includes(value);
}

// 仕上げのフェーズに常に置く工程（振り返りと棚卸し）。step は step.recorded の step の値
export const CLOSING_STEPS = [
  { label: "振り返り", step: "retro" },
  { label: "棚卸し", step: "refine" },
] as const;
