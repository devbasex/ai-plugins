// 要素ごとの実行（#2384 の決定 3・不変条件 I13）。要素を並べる数 10 で順にユースケースへ渡し、
// ApiError を要素の結果に包む。応答は入力と同じ順・同じ数で、失敗の要素はその要素のデータを持たない
import { AccountUnavailableError, ApiError } from "../errors.ts";

// 要素の読み出しを並べる数の上限（一覧の行の読み出しと同じ値）
export const ELEMENT_CONCURRENCY = 10;

export type ElementResult =
  | { ok: true; value: unknown }
  | { ok: false; status: number; error: { code: string; message: string; details?: Record<string, unknown> } };

export async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]!, i);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

// ApiError でない失敗（保存の不調など）も、要求全体でなくその要素の 503 にする（決定 3）。1 要素の失敗で
// 全体を投げると、ほかの要素の書き込みが進んだまま成功の結果（1 度しか返せない委任の値など）が失われる。
// 503 の本文は要求全体の失敗（handlers/api.ts の onError）と同じにする
export function runElements<E>(elements: E[], fn: (element: E) => Promise<unknown>): Promise<ElementResult[]> {
  return mapLimit(elements, ELEMENT_CONCURRENCY, async (element) => {
    try {
      return { ok: true as const, value: await fn(element) };
    } catch (err) {
      const known = err instanceof ApiError ? err : new AccountUnavailableError();
      return { ok: false as const, status: known.status, error: known.body().error };
    }
  });
}
