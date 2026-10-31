// 追記。受け口と appendControlEvent が同じ関数を通り、同じ重複の拒否で 1 件を残す（I10）
import type { IssueRequestResolvedEvent, JobFinishedEvent, ProgressEvent } from "@ndf-factory/contracts";
import { authorizeControl } from "./authorize.ts";
import { ProgressEventError } from "./errors.ts";
import { prRef } from "./github.ts";
import type { ProgressEventRecord, ProgressEventStore } from "./store.ts";
import { checkBuiltEvent } from "./validate.ts";

// 並びの鍵。at をミリ秒の 3 桁の UTC にそろえてから冪等の鍵と結ぶ（決定 6）
export const orderKey = (event: ProgressEvent) => `${new Date(event.at).toISOString()}#${event.idempotency_key}`;

// 事象から表の 1 行を作る。at は送られた値のまま残し、step.recorded が Pull Request を指すときだけ pr_ref を置く（決定 7）
export function toRecord(event: ProgressEvent): ProgressEventRecord {
  const record: ProgressEventRecord = { ...event, order_key: orderKey(event) };
  if (event.type === "step.recorded" && event.payload.pull_request) {
    record.pr_ref = prRef(event.payload.pull_request.repo, event.payload.pull_request.number);
  }
  return record;
}

export function appendEvent(store: ProgressEventStore, event: ProgressEvent): Promise<{ written: boolean }> {
  return store.append(toRecord(event));
}

// 実行の制御（#2186）が job.finished（source は control）を直に追記する。受け口の認証の手前は通らず、
// 契約の検査・control の照合・追記の 3 つを通る（決定 9）。false は同じ鍵（ランナーの分）が既にある
export async function appendControlEventTo(store: ProgressEventStore, event: JobFinishedEvent): Promise<{ written: boolean }> {
  const checked = checkBuiltEvent(event);
  if (!checked.ok) throw new ProgressEventError("invalid_event", checked.message);
  if (checked.event.type !== "job.finished" || !authorizeControl(checked.event)) {
    throw new ProgressEventError("forbidden", "control が書けるのは <job_id>:finished の job.finished だけです");
  }
  return appendEvent(store, checked.event);
}

// 起票の代行（#2216）が issue.request_resolved（source は control、鍵は <依頼の鍵>:resolved）を直に追記する。
// appendControlEventTo と同じ 3 つを通り、受けるのは issue.request_resolved だけ（決定 10）。false は同じ依頼の結果が既にある
export async function appendIssueRequestResolvedTo(
  store: ProgressEventStore,
  event: IssueRequestResolvedEvent,
): Promise<{ written: boolean }> {
  const checked = checkBuiltEvent(event);
  if (!checked.ok) throw new ProgressEventError("invalid_event", checked.message);
  if (checked.event.type !== "issue.request_resolved" || !authorizeControl(checked.event)) {
    throw new ProgressEventError("forbidden", "ここで書けるのは <依頼の鍵>:resolved の issue.request_resolved だけです");
  }
  return appendEvent(store, checked.event);
}
