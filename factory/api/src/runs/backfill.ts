// 移行の埋め戻し（#2347 の決定 12、設計の「データ構造」の「移行」）。この版より前に作られた終わりの状態でない実行を、
// 古い版として legacyCount と課題の持ち分へ写し、legacy の空の実行が 0 件になったら移行の済みの行を書く。
// 一度だけ流し、何度流しても同じ結果になる（legacy の空の実行だけを扱う）
//   RUNS_TABLE_NAME=<表> node src/runs/backfill.ts
import { isTerminalRun } from "./model.ts";
import type { RunsTable } from "./table.ts";

const RETRIES = 5;

export interface BackfillResult {
  backfilled: number;
  migrated: boolean;
}

export async function backfill(table: RunsTable, log: (entry: Record<string, unknown>) => void = () => undefined): Promise<BackfillResult> {
  let backfilled = 0;
  for (const listed of await table.listUnbackfilledRuns()) {
    for (let attempt = 0; attempt < RETRIES; attempt++) {
      const run = await table.getRun(listed.sprintRunId);
      if (!run || isTerminalRun(run.state) || run.legacy !== undefined) break;
      // 他の実行が持つ課題は除く（どちらの実行も古い版で数えられ、終わるまで新しい開始は通らない）
      const held = new Set((await table.listIssueShares(run.accountId, run.repositoryId)).filter((s) => s.sprintRunId !== run.sprintRunId).map((s) => s.number));
      const issues = run.issues.filter((n) => !held.has(n));
      if (await table.backfillRun(run, issues)) {
        backfilled += 1;
        log({ event: "backfilled", sprint_run_id: run.sprintRunId, issues: issues.length });
        break;
      }
    }
  }
  if ((await table.listUnbackfilledRuns()).length > 0) return { backfilled, migrated: false };
  await table.setMigrated();
  return { backfilled, migrated: true };
}

async function main(): Promise<void> {
  const name = process.env.RUNS_TABLE_NAME;
  if (!name) throw new Error("環境変数 RUNS_TABLE_NAME がありません");
  const { DynamoRunsTable } = await import("./dynamo-table.ts");
  const result = await backfill(new DynamoRunsTable(name), (e) => console.log(JSON.stringify(e)));
  console.log(JSON.stringify({ event: "backfill_done", ...result }));
  if (!result.migrated) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) await main();
