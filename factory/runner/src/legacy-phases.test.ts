import { describe, expect, it } from "vitest";
import { normalizePhase } from "./legacy-phases.ts";
import { phaseNameOf } from "./translate.ts";

describe("旧い値のフェーズの読み替え（#2620 の受け入れ条件 7）", () => {
  it("旧い値は新しい値と同じフェーズの名前へ写す", () => {
    expect(phaseNameOf("7-release", "配布（開発版）")).toBe(phaseNameOf("7-release", "開発版のデプロイ"));
    expect(phaseNameOf("7-release", "配布（本番）")).toBe(phaseNameOf("7-release", "リリース"));
  });

  it("表に無い値はそのまま返す", () => {
    expect(normalizePhase("実装")).toBe("実装");
    expect(normalizePhase("配布の依頼")).toBe("リリースの依頼");
  });
});
