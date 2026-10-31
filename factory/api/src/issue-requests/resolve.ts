// 起票の代行（#2216 の「処理の流れ」の起票の代行、I46〜I50）。記録された issue.requested 1 件を判定し、
// 起票してよい先へはサービスが起票し、そうでない先は下書きへ回し、結果の issue.request_resolved を 1 件残す
import type { IssueRequestedEvent, IssueRequestResolvedEvent } from "@ndf-factory/contracts";
import type { CreatedIssue, GitHubPort, RepositoryVisibility } from "../github/port.ts";
import { resolvedKey } from "../progress/authorize.ts";
import { repoKey } from "../progress/github.ts";
import type { Registration, SprintRun } from "../runs/model.ts";
import { requestDigest, type DraftReason, type IssueLedger, type Settled } from "./ledger.ts";
import { containsSecret } from "./secrets.ts";
import type { FilingStyleResolver } from "./style.ts";

// スプリントの実行ごとにサービスが作る Issue の数の既定の上限（決定 12。実測に基づかない初期値）
export const DEFAULT_ISSUE_REQUEST_LIMIT_PER_RUN = 10;

// 見える人の広さの順。internal は企業の中の全員に見え、private より広い
const VISIBILITY_RANK: Record<RepositoryVisibility, number> = { private: 0, internal: 1, public: 2 };

// ログの 1 行。依頼の本文・題・token は載せない
export interface IssueRequestLogEntry {
  type: "issue.request_resolved";
  request_key: string;
  outcome: "filed" | "drafted";
  reason: DraftReason | null;
  duplicate: boolean;
}

export interface IssueRequestDeps {
  // ほかの集約（#2186）を読むだけで書かない
  getSprintRun(sprintRunId: string): Promise<Pick<SprintRun, "accountId" | "repositoryId"> | null>;
  listRegistrations(accountId: string): Promise<Registration[]>;
  github: Pick<GitHubPort, "createIssue" | "repositoryVisibility">;
  style: FilingStyleResolver;
  ledger: IssueLedger;
  append(event: IssueRequestResolvedEvent): Promise<{ written: boolean }>;
  limitPerRun: number;
  now(): string;
  log?(entry: IssueRequestLogEntry): void;
}

export type Resolution = { outcome: "filed"; issue: CreatedIssue } | { outcome: "drafted"; reason: DraftReason };

export async function resolveIssueRequest(request: IssueRequestedEvent, deps: IssueRequestDeps): Promise<Resolution> {
  const { sprint_run_id: runId, idempotency_key: requestKey } = request;
  const { repo, title, body } = request.payload;
  const digest = requestDigest(repo, title, body);

  const claim = await deps.ledger.claim(runId, digest, requestKey, deps.now());
  if (!claim.created) {
    const row = claim.row;
    if (row.state === "filing") {
      // ほかの依頼が起票の途中にある。投げて、その結果が出てからやり直させる
      if (row.requestKey !== requestKey) throw new Error("同じ中身の依頼が起票の途中です");
      // 自分の前の試みが Issue の作成の要求の後で止まった。作ったかどうかわからないため作り直さない（決定 10）
      if (row.attemptedAt !== null) return finish(request, deps, digest, { state: "drafted", reason: "filing_failed" }, null);
    } else {
      // 決着した行。同じ依頼の流し直しなら同じ結果を、同じ中身の別の依頼なら最初の結果を写す（I48）
      const duplicateOf = row.requestKey === requestKey ? null : row.requestKey;
      return record(request, deps, row, duplicateOf);
    }
  }

  return finish(request, deps, digest, await decide(request, deps, digest), null);
}

// 判定の順序は、外へ出す前に安く確かめられるものから（設計の「処理の流れ」）
async function decide(request: IssueRequestedEvent, deps: IssueRequestDeps, digest: string): Promise<Settled> {
  const { sprint_run_id: runId } = request;
  const { repo, title, body } = request.payload;
  const drafted = (reason: DraftReason): Settled => ({ state: "drafted", reason });

  if (containsSecret(title, body)) return drafted("secret_suspected");

  // アカウントは依頼の実行からサービスの表で引く。依頼の値は名前を引く鍵にしか使わない（I46・I50）
  const run = await deps.getSprintRun(runId);
  if (!run) return drafted("not_registered");
  const registrations = await deps.listRegistrations(run.accountId);
  const target = registrations.find((r) => repoKey(r.fullName) === repoKey(repo));
  if (!target) return drafted("not_registered");
  const targetRef = { repo: target.fullName, installationId: target.installationId, repositoryId: target.repositoryId };

  const style = await deps.style.resolve(targetRef);
  if (style.tracker !== "github_issues") return drafted("unsupported_tracker");

  // 公開範囲が広がる向き（起票先の方が実行のリポジトリより広く見える。private < internal < public）は起票しない（決定 11）
  const targetVisibility = await deps.github.repositoryVisibility(target.installationId, target.repositoryId);
  if (targetVisibility === null) return drafted("not_installed");
  if (targetVisibility !== "private") {
    const source = registrations.find((r) => r.repositoryId === run.repositoryId);
    const sourceVisibility = source ? await deps.github.repositoryVisibility(source.installationId, source.repositoryId) : null;
    if (sourceVisibility === null || VISIBILITY_RANK[sourceVisibility] < VISIBILITY_RANK[targetVisibility]) return drafted("visibility");
  }

  // 数は起票の直前に増やし、起票しなかった依頼で枠を使わない（I49）
  if (!(await deps.ledger.reserve(runId, digest, deps.limitPerRun, deps.now()))) return drafted("limit_exceeded");

  // 作成の要求を出した後の失敗はやり直さない（決定 10）
  let created: CreatedIssue | null;
  try {
    created = await deps.github.createIssue(target.installationId, target.repositoryId, { title, body });
  } catch {
    await deps.ledger.release(runId);
    return drafted("filing_failed");
  }
  if (!created) {
    await deps.ledger.release(runId);
    return drafted("not_installed");
  }
  return { state: "filed", issue: { ...created, repo: created.repo || target.fullName } };
}

async function finish(
  request: IssueRequestedEvent,
  deps: IssueRequestDeps,
  digest: string,
  settled: Settled,
  duplicateOf: string | null,
): Promise<Resolution> {
  await deps.ledger.settle(request.sprint_run_id, digest, settled, deps.now());
  return record(request, deps, settled, duplicateOf);
}

// 結果を直に追記する。鍵は <依頼の鍵>:resolved で、依頼 1 件に結果は 1 件（I48）
async function record(request: IssueRequestedEvent, deps: IssueRequestDeps, settled: Settled, duplicateOf: string | null): Promise<Resolution> {
  const requestKey = request.idempotency_key;
  const payload: IssueRequestResolvedEvent["payload"] =
    settled.state === "filed"
      ? { request_key: requestKey, outcome: "filed", issue: settled.issue, duplicate_of: duplicateOf }
      : { request_key: requestKey, outcome: "drafted", reason: settled.reason, duplicate_of: duplicateOf };
  await deps.append({
    sprint_run_id: request.sprint_run_id,
    idempotency_key: resolvedKey(requestKey),
    type: "issue.request_resolved",
    at: deps.now(),
    source: "control",
    job_id: request.job_id,
    schema_version: 1,
    payload,
  });
  deps.log?.({
    type: "issue.request_resolved",
    request_key: requestKey,
    outcome: payload.outcome,
    reason: settled.state === "drafted" ? settled.reason : null,
    duplicate: duplicateOf !== null,
  });
  return settled.state === "filed" ? { outcome: "filed", issue: settled.issue } : { outcome: "drafted", reason: settled.reason };
}
