// 進捗の記録の保存を環境変数から作る
import { requireEnv } from "../env.ts";
import { DynamoProgressEventStore, type ProgressEventStore } from "./store.ts";

let store: ProgressEventStore | null = null;

// 進捗の記録の保存。受け口と appendControlEvent が同じものを使う
export function progressStoreFromEnv(): ProgressEventStore {
  store ??= new DynamoProgressEventStore(
    requireEnv("PROGRESS_EVENTS_TABLE_NAME"),
    requireEnv("PROGRESS_IDEMPOTENCY_TABLE_NAME"),
  );
  return store;
}
