// 送信。5xx と接続の失敗だけを、同じ事象（同じ冪等の鍵）のまま 1・2・4・8 秒…最大 60 秒の間で 30 分まで送り直す。
// 4xx は送り直さず、標準エラーへ 1 行書いて次へ進む
import { validateProgressEvent, type ProgressEvent } from "@ndf-factory/contracts";

export const RETRY_LIMIT_MS = 30 * 60 * 1000;
export const MAX_BACKOFF_MS = 60_000;

export type SendResult = "sent" | "rejected" | "gave_up";

export interface SenderDeps {
  url: string;
  token: () => string;
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  stderr?: (line: string) => void;
}

export function createSender(deps: SenderDeps): (event: ProgressEvent) => Promise<SendResult> {
  const http = deps.fetch ?? fetch;
  const sleep = deps.sleep ?? ((ms) => new Promise<void>((r) => setTimeout(r, ms)));
  const now = deps.now ?? Date.now;
  const stderr = deps.stderr ?? ((line) => process.stderr.write(`${line}\n`));
  return async (event) => {
    const checked = validateProgressEvent(event);
    if (!checked.ok) {
      stderr(`契約に合わない事象を送らない: ${event.type} ${checked.violation.instancePath} ${checked.violation.keyword}`);
      return "rejected";
    }
    const body = JSON.stringify(event);
    const startedAt = now();
    let wait = 1000;
    for (;;) {
      let status: number | null = null;
      try {
        const res = await http(deps.url, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${deps.token()}` },
          body,
        });
        status = res.status;
      } catch {
        status = null;
      }
      if (status !== null && status < 400) return "sent";
      if (status !== null && status < 500) {
        stderr(`受け口が ${status} で断った: ${event.type} ${event.idempotency_key}`);
        return "rejected";
      }
      if (now() - startedAt + wait > RETRY_LIMIT_MS) return "gave_up";
      await sleep(wait);
      wait = Math.min(wait * 2, MAX_BACKOFF_MS);
    }
  };
}
