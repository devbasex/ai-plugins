// 承認の依頼の保存済みの答えを読む口（決定 10）。答えは #2173 の集約で、#2173 がこのファイルの
// savedAnswersFromEnv の中身を本物の読み出しに替える。受け口のほかのファイルは変えずに済む
import type { GateKind } from "@ndf-factory/contracts";

export interface SavedAnswer {
  approvalRequestId: string;
  sprintRunId: string;
  kind: GateKind;
  // 判断（kind が decision）では持たない
  decision?: "approved" | "rejected";
  answeredByAccountId: string;
}

// 依頼の識別子から保存済みの答えを返す。null は未保存。読めないときは投げる（受け口が 503 にする）
export type SavedAnswerReader = (approvalRequestId: string) => Promise<SavedAnswer | null>;

export class SavedAnswersUnavailableError extends Error {
  constructor() {
    super("承認の依頼の保存済みの答えを読む経路がまだありません（#2173）");
    this.name = "SavedAnswersUnavailableError";
  }
}

// #2173 が実装するまでの既定。常に「読めない」として投げ、api の経路は 503 を返す
export function savedAnswersFromEnv(): SavedAnswerReader {
  return () => Promise.reject(new SavedAnswersUnavailableError());
}
