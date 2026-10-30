// 事象の検査: 大きさ・JSON・契約の定義（決定 3・8、I29）
import { validateProgressEvent, type ContractViolation, type ProgressEvent } from "@ndf-factory/contracts";

// 事象 1 件の上限（256 KiB）。DynamoDB の項目の上限（400 KB）より下で、列を足しても当たらない（決定 8）
export const MAX_EVENT_BYTES = 262_144;

export type EventCheck = { ok: true; event: ProgressEvent } | { ok: false; message: string };

const describe = (v: ContractViolation) => `${v.instancePath || "/"}: ${v.keyword}`;

export const byteLength = (text: string) => Buffer.byteLength(text, "utf8");

// 要求の本文（runner・api の経路）を大きさと JSON で確かめて読む
export function parseEventBody(raw: string): { ok: true; value: unknown } | { ok: false; message: string } {
  if (byteLength(raw) > MAX_EVENT_BYTES) return { ok: false, message: `/: maxBytes ${MAX_EVENT_BYTES}` };
  try {
    return { ok: true, value: JSON.parse(raw) as unknown };
  } catch {
    return { ok: false, message: "/: json" };
  }
}

// 契約の定義で確かめる
export function checkEvent(value: unknown): EventCheck {
  const result = validateProgressEvent(value);
  return result.ok ? { ok: true, event: result.value } : { ok: false, message: describe(result.violation) };
}

// 本文を経ずに作った事象（github の翻訳・直の追記）を、定義と JSON にしたバイト数で確かめる
export function checkBuiltEvent(value: unknown): EventCheck {
  const checked = checkEvent(value);
  if (!checked.ok) return checked;
  if (byteLength(JSON.stringify(value)) > MAX_EVENT_BYTES) return { ok: false, message: `/: maxBytes ${MAX_EVENT_BYTES}` };
  return checked;
}
