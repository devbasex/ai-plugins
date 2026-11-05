import { AccountUnavailableError } from "../errors.ts";
import { ConditionFailedError } from "./port.ts";

// 版の競合で読み直して書き直す回数の上限（docs/specifications/ndf-factory-accounts-and-plans.md の「常に成り立つ条件」）
const VERSION_RETRIES = 3;

// 読み直しと判定の 1 回分の結果。書かずに終わる（done）か、書いて終わる（write が成功したら result）
type ConflictStep<T> = { done: T } | { write: () => Promise<void>; result: T };

// 版の競合（条件の失敗）なら step から読み直して上限の回数まで書き直す。step の中で投げた誤りはそのまま届く。
// 条件以外の書き込みの失敗は onOther の結果を投げる（既定は AccountUnavailableError）
export async function retryOnConflict<T>(
  step: () => Promise<ConflictStep<T>>,
  onOther: (err: unknown) => unknown = () => new AccountUnavailableError(),
): Promise<T> {
  for (let attempt = 0; attempt < VERSION_RETRIES; attempt++) {
    const next = await step();
    if ("done" in next) return next.done;
    try {
      await next.write();
      return next.result;
    } catch (err) {
      if (!(err instanceof ConditionFailedError)) throw onOther(err);
    }
  }
  throw new AccountUnavailableError();
}

// 再試行しない書き込み。成功なら undefined、条件の失敗なら onCondition の結果を返す（onCondition は投げてもよい）。
// 条件以外の失敗は AccountUnavailableError
export async function writeOr<T>(write: () => Promise<void>, onCondition: () => T | Promise<T>): Promise<T | undefined> {
  try {
    await write();
    return undefined;
  } catch (err) {
    if (!(err instanceof ConditionFailedError)) throw new AccountUnavailableError();
  }
  return onCondition();
}
