// 配布のまとめ（OrganizerControl。#2439 の F2〜F8）。同じリポジトリで並行する実行の配布の依頼を受け、まとめた配布を 1 つずつ組み、
// 配布の実行を起こし、承認の答えで出し直すか確定し、結果を範囲の実行へ返す。常駐せず、実行の制御の停止の後の処理・再開の要求・
// 見回りと、画面の経路の中で動く（非機能の「可用性」）。状態はすべて実行の表の行で、tick は根の状態だけで次の 1 手が決まる
import type { ReleaseAnsweredEvent } from "@ndf-factory/contracts";
import type { AccountContext } from "../accounts/model.ts";
import { ConflictError, InvalidRequestError, NotFoundError, ServiceUnavailableError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import { releaseAnsweredKey } from "../progress/authorize.ts";
import type { ProgressReader, ReleaseEvents } from "../progress/read.ts";
import type { RunControl } from "../runs/control.ts";
import { isTerminalRun, type Registration, type ReleaseStep, type SprintRun } from "../runs/model.ts";
import type { RunsTable } from "../runs/table.ts";
import {
  choicesOf,
  emptyRoot,
  isTerminalBatch,
  laneOf,
  newestVersion,
  presentedVersion,
  requestsToAnswer,
  resumeStepOf,
  stalledFromOf,
  stepFrom,
  type BatchLane,
  type OrganizerRoot,
  type OrgLog,
  type OrgLogKind,
  type OrgRequest,
  type ReleaseAnswer,
  type ReleaseBatch,
} from "./model.ts";

// 根の状態を変える書き込みが落ちたときに読み直す回数（I67）
const ROOT_RETRIES = 3;
// 起動の記録が無いまま pending の配布の実行のジョブを起こし直すまでの時間（組んだ直後の起動と取り合わない）
const RELAUNCH_AFTER_MS = 60_000;

export interface OrganizerDeps {
  table: RunsTable;
  github: GitHubPort;
  reader: ProgressReader;
  control: RunControl;
  appendAnswered: (event: ReleaseAnsweredEvent) => Promise<{ written: boolean }>;
  now?: () => Date;
  log?: (entry: Record<string, unknown>) => void;
}

// 自分で開いた本番の枝を探す、差し戻したまとめた配布の件数（新しい方から）
const OWN_RELEASE_LOOKBACK = 20;

const isExternalRelease = (head: string) => head.startsWith("release/v") || head === "develop";

export class OrganizerControl {
  private readonly now: () => Date;
  private readonly log: (entry: Record<string, unknown>) => void;

  constructor(private readonly deps: OrganizerDeps) {
    this.now = deps.now ?? (() => new Date());
    this.log = deps.log ?? ((entry) => console.log(JSON.stringify(entry)));
  }

  private iso(): string {
    return this.now().toISOString();
  }

  private logRow(root: Pick<OrganizerRoot, "accountId" | "repositoryId">, kind: OrgLogKind, seq: number | null, detail: Record<string, unknown>): OrgLog {
    return { accountId: root.accountId, repositoryId: root.repositoryId, at: this.iso(), kind, seq, detail };
  }

  // ---- 受ける（停止の後の処理が呼ぶ） ----

  // ジョブが送った release.requested から、まだ無い受けた依頼の行を作る（I59）。実行の移りと同じトランザクションで書く
  async newRequestRows(run: SprintRun, events: ReleaseEvents): Promise<OrgRequest[]> {
    if (events.requested.length === 0) return [];
    const existing = new Set((await this.deps.table.listOrgRequests(run.accountId, run.repositoryId)).map((r) => r.requestId));
    const out: OrgRequest[] = [];
    for (const p of events.requested) {
      if (existing.has(p.request_id) || out.some((r) => r.requestId === p.request_id)) continue;
      out.push({
        accountId: run.accountId,
        repositoryId: run.repositoryId,
        requestId: p.request_id,
        sprintRunId: run.sprintRunId,
        loopId: run.loopId ?? null,
        lane: p.lane,
        prs: p.prs,
        issues: p.issues,
        ndfVersion: p.ndf_version,
        requestedAt: p.requested_at,
        answer: null,
        deliveredAt: null,
      });
    }
    return out;
  }

  // ---- tick: 根の続き（届け直し・流れている配布の続き・待ちの判定と組む） ----

  async tick(accountId: string, repositoryId: number): Promise<void> {
    for (let attempt = 0; attempt < ROOT_RETRIES; attempt++) {
      await this.deliverAll(accountId, repositoryId);
      const root = (await this.deps.table.getOrganizer(accountId, repositoryId)) ?? null;
      const state = root ?? emptyRoot(accountId, repositoryId, this.iso());
      if (state.currentSeq !== null) {
        const batch = await this.deps.table.getBatch(accountId, repositoryId, state.currentSeq);
        if (!batch) return;
        const moved = await this.advance(state, batch);
        // 流れている配布が終わったら、残った依頼で次のまとめた配布を組む（同じ tick で続ける）
        if (moved === "ended") continue;
        if (moved === "retry") continue;
        return;
      }
      const composed = await this.compose(root, state);
      if (composed !== "retry") return;
    }
  }

  // 流れているまとめた配布を、配布の実行の状態と事象に合わせて 1 手進める
  private async advance(root: OrganizerRoot, batch: ReleaseBatch): Promise<"ended" | "retry" | "same"> {
    const { table, reader } = this.deps;
    const run = await table.getRun(batch.releaseRunId);
    if (!run) return "same";
    const events = await reader.findReleaseEvents(run.sprintRunId);
    let next: ReleaseBatch = structuredClone(batch);
    const logs: OrgLog[] = [];

    // 提示（E3・E12）。提示は上書きせず足す。3 つ目は組まない（I60）
    for (const p of events.presented) {
      if (p.seq !== batch.seq || next.presentations.some((x) => x.k === p.k) || p.k !== next.presentations.length + 1) continue;
      next.presentations.push({ k: p.k as 1 | 2, sha: p.sha, version: p.version, prs: p.prs, lanes: p.lanes, addedPrs: p.added_prs, at: this.iso() });
      if (p.auto) {
        next.state = "confirmed";
        next.confirmedK = p.k as 1 | 2;
        logs.push(this.logRow(root, "confirmed", batch.seq, { k: p.k, sha: p.sha, auto: true }));
      } else if (next.state === "composed" || next.state === "represented") {
        next.state = "presented";
        logs.push(this.logRow(root, "presented", batch.seq, { k: p.k, sha: p.sha, prs: p.prs, added_prs: p.added_prs }));
      }
    }
    if (!next.mainMerged && events.progress.some((p) => p.seq === batch.seq && p.stage === "main_merged")) next.mainMerged = true;

    // 結果（E7）
    const settled = events.settled && events.settled.seq === batch.seq ? events.settled : null;
    if (settled?.result === "released" && !isTerminalBatch(next.state)) {
      next.state = "released";
      next.mainMerged = true;
      next.result = { version: settled.version ?? null, prodPrUrl: settled.prod_pr_url ?? null, sha: settled.sha ?? null, prs: settled.prs };
      next.stalledFrom = null;
      next.stallReason = null;
      const ok = await this.end(root, batch, next, "released", settled.prs, this.logRow(root, "released", batch.seq, { ...next.result }));
      return ok ? "ended" : "retry";
    }

    // 配布の実行が止まった（I65・決定 11）
    if (run.state === "awaiting_approval" && run.awaitingReason === "release_stalled" && next.state !== "stalled") {
      const from = stalledFromOf(next.state);
      if (from) {
        next.mainMerged = next.mainMerged || (await this.mergedOnGitHub(run, next));
        next.stalledFrom = from;
        next.stallReason = settled?.reason ?? run.failureReason ?? "job_failed";
        next.state = "stalled";
        logs.push(this.logRow(root, "stalled", batch.seq, { from, reason: next.stallReason, main_merged: next.mainMerged }));
      }
    }

    // 配布の実行が取り消しなどで終わった。main へのマージの前なら差し戻す（I63）。ランナーが段を送る前に取り消されても
    // 公開済みの配布を差し戻さないよう、GitHub の main へのマージを照らしてから決める
    if (isTerminalRun(run.state) && !isTerminalBatch(next.state) && !next.mainMerged) next.mainMerged = await this.mergedOnGitHub(run, next);
    if (isTerminalRun(run.state) && !isTerminalBatch(next.state) && !next.mainMerged) {
      next.state = "rejected";
      const ok = await this.end(root, batch, next, "rejected", [], this.logRow(root, "rejected", batch.seq, { reason: `release_run_${run.state}` }));
      return ok ? "ended" : "retry";
    }

    if (JSON.stringify(next) !== JSON.stringify(batch)) {
      next.updatedAt = this.iso();
      const ok = await table.writeOrganizer({
        root: { ...root, version: root.version + 1, updatedAt: this.iso() },
        expectVersion: root.version,
        batch: next,
        expectBatchState: [batch.state],
        ...(logs[0] ? { log: logs[0] } : {}),
      });
      if (!ok) return "retry";
      for (const extra of logs.slice(1)) await table.appendOrgLog(extra);
    }

    // 組んだ後に起動の前で止まった配布の実行を起こし直す（ジョブの冪等は startedBy）
    if (run.state === "starting" && run.currentJobId) {
      const job = await table.getJob(run.currentJobId);
      if (
        job &&
        job.state === "pending" &&
        job.runnerKind === "ecs" &&
        !job.taskArn &&
        !job.tokenSecretArn &&
        this.now().getTime() - Date.parse(job.createdAt) > RELAUNCH_AFTER_MS
      ) {
        await this.deps.control.launchJob(run, job);
      }
    }
    return "same";
  }

  // 本番のプランに手を付けた跡（届いた段の事象か、確定した提示の release/v<版> の枝・main 宛ての Pull Request）。
  // 差し戻したまとめた配布が残した同じ版の枝は跡に数えない（出していないので次の配布は同じ版を提示する）
  private async releaseStarted(run: SprintRun, batch: ReleaseBatch, reg: Registration, checkGitHub = true): Promise<boolean> {
    const { reader, github } = this.deps;
    if ((await reader.findReleaseEvents(run.sprintRunId)).progress.length > 0) return true;
    const version = checkGitHub ? presentedVersion(batch) : null;
    if (!version) return false;
    const branch = `release/v${version}`;
    if ((await this.rejectedReleaseHeads(batch)).has(branch)) return false;
    return (
      (await github.branchHead(reg.installationId, reg.repositoryId, reg.fullName, branch)) !== null ||
      (await github.findPullByHead(reg.installationId, reg.repositoryId, reg.fullName, branch, "main")) !== null
    );
  }

  // main へのマージを GitHub で照らす（ジョブが段を書く前に落ちても分かるように。決定 11）
  private async mergedOnGitHub(run: SprintRun, batch: ReleaseBatch): Promise<boolean> {
    const version = presentedVersion(batch);
    if (!version) return false;
    const reg = await this.deps.table.getRegistration(run.accountId, run.repositoryId);
    if (!reg) return false;
    const pull = await this.deps.github.findPullByHead(reg.installationId, reg.repositoryId, reg.fullName, `release/v${version}`, "main");
    return pull?.state === "merged";
  }

  // まとめた配布を終わりの状態にして根を空け、範囲の依頼へ答えを書く（I58・I61）
  private async end(root: OrganizerRoot, before: ReleaseBatch, next: ReleaseBatch, end: "released" | "rejected", settledPrs: number[], log: OrgLog): Promise<boolean> {
    const { table } = this.deps;
    next.updatedAt = this.iso();
    const ok = await table.writeOrganizer({
      root: { ...root, currentSeq: null, version: root.version + 1, updatedAt: this.iso() },
      expectVersion: root.version,
      batch: next,
      expectBatchState: [before.state],
      log,
    });
    if (!ok) return false;
    await this.answerRange(next, end, settledPrs);
    if (end === "rejected") {
      const run = await table.getRun(next.releaseRunId);
      if (run && !isTerminalRun(run.state)) await table.finishRun(run.sprintRunId, "stopped", null, this.iso());
    }
    return true;
  }

  // 範囲の依頼へ答えを書く。終わった後に止まっても、見回りが同じ規則で書き直す（2 回目は I58 で書かれない）
  // before を渡すと、その時刻までに出した依頼だけを照らす（終わった後に出し直した依頼を古い配布で答えない）
  // known を渡すと読み直さずにそれを照らし、書けた答えをその行へ写す。別の書き手と取り合ったら false（呼び手が読み直す）
  private async answerRange(batch: ReleaseBatch, end: "released" | "rejected", settledPrs: number[], before?: string, known?: OrgRequest[]): Promise<boolean> {
    const requests = (known ?? (await this.deps.table.listOrgRequests(batch.accountId, batch.repositoryId))).filter(
      (r) => before === undefined || Date.parse(r.requestedAt) <= Date.parse(before),
    );
    const targets = requestsToAnswer(batch, requests, end, settledPrs);
    let fresh = true;
    for (const r of targets) {
      const answer: ReleaseAnswer = {
        seq: batch.seq,
        result: end,
        version: end === "released" ? (batch.result?.version ?? null) : null,
        prodPrUrl: end === "released" ? (batch.result?.prodPrUrl ?? null) : null,
        reason: end === "rejected" ? "release_rejected" : null,
        at: this.iso(),
      };
      if (await this.deps.table.answerRequest(r.accountId, r.repositoryId, r.requestId, answer)) r.answer = answer;
      else fresh = false;
    }
    return fresh;
  }

  // 答えがあって届け終えていない依頼を届ける（E8）。release.answered を追記し、手動の実行は再開する。
  // 実行がまだ別のジョブで動いている（busy）ときは届け終えたことにせず、次の tick で届け直す
  private async deliverAll(accountId: string, repositoryId: number): Promise<void> {
    const { table, control } = this.deps;
    // 終わった配布の答えを書き終える前に止まっていたら、ここで書き直す。依頼の一覧は 1 回だけ読み、書いた答えはその行へ写す
    let requests = await table.listOrgRequests(accountId, repositoryId);
    let fresh = true;
    for (const batch of await table.listBatches(accountId, repositoryId, 5)) {
      if (batch.state === "released" || batch.state === "rejected") {
        if (!requests.some((r) => r.answer === null)) break;
        if (!(await this.answerRange(batch, batch.state, batch.result?.prs ?? [], batch.updatedAt, requests))) fresh = false;
      }
    }
    if (!fresh) requests = await table.listOrgRequests(accountId, repositoryId);
    for (const r of requests) {
      if (!r.answer || r.deliveredAt !== null) continue;
      const event: ReleaseAnsweredEvent = {
        sprint_run_id: r.sprintRunId,
        idempotency_key: releaseAnsweredKey(r.answer.seq, r.sprintRunId),
        type: "release.answered",
        at: r.answer.at,
        source: "control",
        job_id: null,
        schema_version: 1,
        payload: {
          seq: r.answer.seq,
          request_id: r.requestId,
          result: r.answer.result,
          ...(r.answer.version ? { version: r.answer.version } : {}),
          ...(r.answer.prodPrUrl ? { prod_pr_url: r.answer.prodPrUrl } : {}),
        },
      };
      await this.deps.appendAnswered(event);
      if (r.loopId === null) {
        const resumed = await control.resumeForRelease(r.sprintRunId, r.requestId);
        if (resumed === "busy") continue;
      }
      await table.markDelivered(accountId, repositoryId, r.requestId, this.iso());
    }
  }

  // 流れている配布が無いとき: 未回答の依頼があれば、待ちの理由を照らしてから組む（F2・I62）
  private async compose(stored: OrganizerRoot | null, root: OrganizerRoot): Promise<"done" | "retry"> {
    const { table, github, control } = this.deps;
    const requests = await table.listOrgRequests(root.accountId, root.repositoryId);
    const pending = requests.filter((r) => r.answer === null);
    if (pending.length === 0) {
      if (stored && stored.waitReason !== null) {
        await table.writeOrganizer({ root: { ...stored, waitReason: null, waitDetail: null, version: stored.version + 1, updatedAt: this.iso() }, expectVersion: stored.version });
      }
      return "done";
    }
    const reg = await table.getRegistration(root.accountId, root.repositoryId);
    if (!reg) return "done";

    // Factory の外の本番の Pull Request（I62・決定 8）。差し戻したまとめた配布が自分で開いた release/v<版> は外のものと見なさない
    // （本番の Pull Request を開いた後に止まって差し戻すと開いたまま残り、数えると人が閉じるまで次の配布が待ち続ける）
    const candidates = (await github.listOpenPullHeads(reg.installationId, reg.repositoryId, reg.fullName, "main")).filter((p) => isExternalRelease(p.head));
    const own = candidates.length > 0 ? await this.rejectedReleaseHeads(root) : new Set<string>();
    const external = candidates.find((p) => !own.has(p.head));
    if (external) return this.wait(stored, root, "external_release_open", external.url);

    // 依頼を出していない古い版の実行（依頼を出して待つ実行は自分で配布しないため数えない）
    const slots = await table.getSlots(root.accountId, root.repositoryId);
    let requestedLegacy = 0;
    const runs = new Map<string, SprintRun>();
    for (const id of new Set(pending.map((r) => r.sprintRunId))) {
      const run = await table.getRun(id);
      if (!run) continue;
      runs.set(id, run);
      if (run.legacy === true && run.state === "awaiting_approval" && run.awaitingReason === "release_requested") requestedLegacy += 1;
    }
    const legacyOpen = (slots.legacy ?? 0) - requestedLegacy;
    if (legacyOpen > 0) return this.wait(stored, root, "legacy_run", String(legacyOpen));

    // レーン: 依頼のあるレーンと、同じリポジトリで動いている依頼の無いレーン
    const lanes = new Map<string, BatchLane>();
    for (const r of pending) {
      const lane = lanes.get(r.lane) ?? { lane: r.lane, sprintRunId: r.sprintRunId, loopId: r.loopId, requestIds: [], issues: [] };
      lane.requestIds.push(r.requestId);
      lane.issues = [...new Set([...lane.issues, ...r.issues])];
      lanes.set(r.lane, lane);
    }
    for (const run of await table.listRuns(root.accountId, 1000)) {
      if (run.repositoryId !== root.repositoryId || run.purpose === "release" || isTerminalRun(run.state)) continue;
      const loopName = run.loopId ? ((await table.getLoop(run.accountId, run.loopId))?.name ?? null) : null;
      const name = laneOf(run, loopName);
      if (!lanes.has(name)) lanes.set(name, { lane: name, sprintRunId: run.sprintRunId, loopId: run.loopId ?? null, requestIds: [], issues: run.issues });
    }

    const base = runs.get(pending[0]!.sprintRunId) ?? [...runs.values()][0];
    if (!base) return "done";
    const seq = root.nextSeq;
    const ndfVersion = newestVersion(pending.map((r) => r.ndfVersion), base.ndfVersion);
    const { run, job } = control.buildReleaseRun(base, seq, ndfVersion);
    const now = this.iso();
    const batch: ReleaseBatch = {
      accountId: root.accountId,
      repositoryId: root.repositoryId,
      seq,
      state: "composed",
      releaseRunId: run.sprintRunId,
      lanes: [...lanes.values()],
      presentations: [],
      confirmedK: null,
      mainMerged: false,
      result: null,
      stallReason: null,
      stalledFrom: null,
      createdAt: now,
      updatedAt: now,
    };
    const ok = await table.composeRelease({
      write: {
        root: { ...root, currentSeq: seq, nextSeq: seq + 1, version: root.version + 1, waitReason: null, waitDetail: null, updatedAt: now },
        expectVersion: stored ? stored.version : null,
        batch,
        log: this.logRow(root, "composed", seq, { release_run_id: run.sprintRunId, lanes: batch.lanes.map((l) => ({ lane: l.lane, requested: l.requestIds.length > 0 })) }),
      },
      run,
      job,
      legacySeq: slots.legacySeq ?? null,
    });
    if (!ok) return "retry";
    await control.launchJob(run, job);
    return "done";
  }

  // 差し戻したまとめた配布の本番の枝（release/v<版>）。新しい方から OWN_RELEASE_LOOKBACK 件を見る
  private async rejectedReleaseHeads(root: Pick<OrganizerRoot, "accountId" | "repositoryId">): Promise<Set<string>> {
    const batches = await this.deps.table.listBatches(root.accountId, root.repositoryId, OWN_RELEASE_LOOKBACK);
    const versions = batches.filter((b) => b.state === "rejected").map((b) => presentedVersion(b));
    return new Set(versions.filter((v): v is string => v !== null).map((v) => `release/v${v}`));
  }

  private async wait(stored: OrganizerRoot | null, root: OrganizerRoot, reason: "external_release_open" | "legacy_run", detail: string): Promise<"done" | "retry"> {
    if (stored && stored.waitReason === reason && stored.waitDetail === detail) return "done";
    const ok = await this.deps.table.writeOrganizer({
      root: { ...root, waitReason: reason, waitDetail: detail, version: root.version + 1, updatedAt: this.iso() },
      expectVersion: stored ? stored.version : null,
      log: this.logRow(root, "waited", null, { reason, detail }),
    });
    return ok ? "done" : "retry";
  }

  // ---- 答え（再開の要求が配布の実行を回す。F4） ----

  // 配布の実行への承認の依頼の答えから、再開の手を決める。差し戻しなら null（ジョブを起こさない）
  async decide(run: SprintRun, approvalRequestId: string): Promise<ReleaseStep | null> {
    const { table, reader, github } = this.deps;
    // 提示の記録が停止の後の処理で書けていなければ先に進める
    await this.tick(run.accountId, run.repositoryId);
    const answer = await reader.findGateAnswer(run.sprintRunId, approvalRequestId);
    if (!answer) throw new ServiceUnavailableError("approval_answer_unavailable");
    for (let attempt = 0; attempt < ROOT_RETRIES; attempt++) {
      const root = await table.getOrganizer(run.accountId, run.repositoryId);
      const batch = run.releaseSeq ? await table.getBatch(run.accountId, run.repositoryId, run.releaseSeq) : null;
      if (!root || !batch) throw new ConflictError("release_not_found", "まとめた配布が見つかりません");
      // 判断（承認ゲートの外の問い）は、止まったプランをまとめた配布の状態から流し直す（止まった配布の「続ける」と同じ規則）。
      // 常に continue だと、提示のプランの中で止まった配布が承認ゲート 2 を通らずに本番のプランへ進む
      if (answer.kind !== "gate2") {
        const from = stalledFromOf(batch.state);
        if (!from) throw new ConflictError("not_presented", "まとめた配布は判断の答えで続けられる状態にありません");
        const reg = from === "confirmed" ? await table.getRegistration(run.accountId, run.repositoryId) : null;
        const started = from === "confirmed" && (batch.mainMerged || (reg !== null && (await this.releaseStarted(run, batch, reg))));
        return stepFrom(from, batch, started);
      }
      // 同じ答えの再送（ジョブを作る前に止まった）は、書いた状態から同じ手を返す。その提示に記録した承認の依頼と一致し、
      // 実行が今も承認ゲート 2 で止まっているときだけにする（本番のプランの中の判断で止まった後の古い答えで流し直さない）
      if (batch.state === "represented" || batch.state === "confirmed") {
        const k = batch.state === "represented" ? 1 : batch.confirmedK;
        const answered = batch.presentations.find((p) => p.k === k);
        const gate = await reader.findLastGate(run.sprintRunId);
        if (!answered?.approvalRequestId || answered.approvalRequestId !== approvalRequestId || gate?.payload.kind !== "gate2") {
          throw new ConflictError("not_presented", "この承認は今待っている提示への答えではありません");
        }
        if (batch.state === "represented") return "represent";
        return k === 2 ? "confirm:2" : "confirm:1";
      }
      if (batch.state !== "presented") throw new ConflictError("not_presented", "まとめた配布は承認を待っていません");
      // 前の提示へ答えた承認の再送は、今待っている提示の承認にしない
      if (batch.presentations.some((p) => p.approvalRequestId === approvalRequestId)) {
        throw new ConflictError("not_presented", "この承認は前の提示への答えです。今の提示は承認を待っています");
      }
      if (answer.decision === "rejected") {
        const next: ReleaseBatch = { ...structuredClone(batch), state: "rejected" };
        if (await this.end(root, batch, next, "rejected", [], this.logRow(root, "rejected", batch.seq, { approval_request_id: approvalRequestId }))) {
          await this.tick(run.accountId, run.repositoryId);
          return null;
        }
        continue;
      }
      const last = batch.presentations.at(-1);
      if (!last) throw new ConflictError("not_presented", "まとめた配布は承認を待っていません");
      let step: ReleaseStep;
      const next: ReleaseBatch = { ...structuredClone(batch), updatedAt: this.iso() };
      next.presentations.at(-1)!.approvalRequestId = approvalRequestId;
      let log: OrgLog;
      if (last.k === 1) {
        // 提示 1 への承認だけ develop の先頭を比べる（I60・決定 7）
        const reg = await table.getRegistration(run.accountId, run.repositoryId);
        const head = reg ? await github.branchHead(reg.installationId, reg.repositoryId, reg.fullName, "develop") : null;
        if (head === null) throw new ServiceUnavailableError("develop_unreadable");
        if (head !== last.sha) {
          next.state = "represented";
          step = "represent";
          log = this.logRow(root, "represented", batch.seq, { from: last.sha, head });
        } else {
          next.state = "confirmed";
          next.confirmedK = 1;
          step = "confirm:1";
          log = this.logRow(root, "confirmed", batch.seq, { k: 1, sha: last.sha });
        }
      } else {
        next.state = "confirmed";
        next.confirmedK = 2;
        step = "confirm:2";
        log = this.logRow(root, "confirmed", batch.seq, { k: 2, sha: last.sha });
      }
      const ok = await table.writeOrganizer({
        root: { ...root, version: root.version + 1, updatedAt: this.iso() },
        expectVersion: root.version,
        batch: next,
        expectBatchState: ["presented"],
        log,
      });
      if (ok) return step;
    }
    throw new ServiceUnavailableError("organizer_busy");
  }

  // ---- 画面の経路（F6・F7） ----

  private async registration(ctx: AccountContext, repositoryId: number): Promise<Registration> {
    const reg = await this.deps.table.getRegistration(ctx.accountId, repositoryId);
    if (!reg) throw new NotFoundError("repository_not_found", "登録したリポジトリが見つかりません");
    return reg;
  }

  private releaseSummary(batch: ReleaseBatch, requests: OrgRequest[], choices: ("continue" | "reject")[]) {
    const last = batch.presentations.at(-1) ?? null;
    return {
      seq: batch.seq,
      k: last?.k ?? null,
      sha: last?.sha ?? null,
      lanes: batch.lanes.map((l) => ({
        lane: l.lane,
        requested: l.requestIds.length > 0,
        prs: [...new Set(requests.filter((r) => l.requestIds.includes(r.requestId)).flatMap((r) => r.prs))],
      })),
      added_prs: last?.addedPrs ?? [],
      choices,
    };
  }

  // リポジトリの承認と問いの一覧（AC 9）。読むだけで、承認の依頼へ答えを書く経路を持たない（I66）
  async list(ctx: AccountContext, repositoryId: number) {
    const { table, reader } = this.deps;
    await this.registration(ctx, repositoryId);
    const root = await table.getOrganizer(ctx.accountId, repositoryId);
    const requests = await table.listOrgRequests(ctx.accountId, repositoryId);
    const items: Record<string, unknown>[] = [];
    for (const run of await table.listRuns(ctx.accountId, 1000)) {
      if (run.repositoryId !== repositoryId || run.state !== "awaiting_approval") continue;
      if (run.awaitingReason === "release_requested") continue;
      const batch = run.purpose === "release" && run.releaseSeq ? await table.getBatch(ctx.accountId, repositoryId, run.releaseSeq) : null;
      if (run.awaitingReason === "release_stalled") {
        if (!batch) continue;
        items.push({ kind: "release_stalled", sprint_run_id: run.sprintRunId, created_at: run.updatedAt, release: this.releaseSummary(batch, requests, choicesOf(batch)) });
        continue;
      }
      const gate = await reader.findLastGate(run.sprintRunId);
      if (!gate) continue;
      items.push({
        kind: gate.payload.kind,
        sprint_run_id: run.sprintRunId,
        ...(run.loopId ? { loop_id: run.loopId } : {}),
        pr_url: gate.payload.pr_url,
        created_at: gate.at,
        ...(batch ? { release: this.releaseSummary(batch, requests, gate.payload.choices ?? []) } : {}),
      });
    }
    for (const loop of await table.listLoops(ctx.accountId)) {
      if (loop.repositoryId !== repositoryId) continue;
      for (const p of loop.pending) items.push({ kind: "loop_pending", loop_id: loop.loopId, number: p.number, reason: p.reason, created_at: loop.updatedAt });
    }
    items.sort((a, b) => ((a.created_at as string) < (b.created_at as string) ? -1 : (a.created_at as string) > (b.created_at as string) ? 1 : 0));
    return {
      organizer: { current_seq: root?.currentSeq ?? null, wait_reason: root?.waitReason ?? null, wait_detail: root?.waitDetail ?? null },
      items,
    };
  }

  // まとめた配布の履歴（新しい順）
  async history(ctx: AccountContext, repositoryId: number, limit: number) {
    const { table } = this.deps;
    await this.registration(ctx, repositoryId);
    const logs = await table.listOrgLogs(ctx.accountId, repositoryId);
    const batches = await table.listBatches(ctx.accountId, repositoryId, limit);
    return {
      items: batches.map((b) => ({
        seq: b.seq,
        state: b.state,
        release_run_id: b.releaseRunId,
        presentations: b.presentations.map((p) => ({ k: p.k, sha: p.sha, version: p.version, prs: p.prs, lanes: p.lanes, added_prs: p.addedPrs, at: p.at })),
        confirmed_k: b.confirmedK,
        result: b.result ? { version: b.result.version, prod_pr_url: b.result.prodPrUrl, sha: b.result.sha, prs: b.result.prs } : null,
        log: logs.filter((l) => l.seq === b.seq).map((l) => ({ at: l.at, kind: l.kind, detail: l.detail })),
      })),
    };
  }

  // 止まった配布を解く（F7）。main へのマージの後の差し戻しは断る（I63）
  async resolve(ctx: AccountContext, repositoryId: number, seq: number, input: { choice?: unknown }) {
    const { table, reader, github, control } = this.deps;
    const choice = input.choice;
    if (choice !== "continue" && choice !== "reject") throw new InvalidRequestError("choice は continue か reject です");
    const reg = await this.registration(ctx, repositoryId);
    // 止まった印が停止の後の処理で書けていなければ先に進める
    await this.tick(ctx.accountId, repositoryId);
    for (let attempt = 0; attempt < ROOT_RETRIES; attempt++) {
      const root = await table.getOrganizer(ctx.accountId, repositoryId);
      const batch = await table.getBatch(ctx.accountId, repositoryId, seq);
      if (!root || !batch) throw new NotFoundError("release_not_found", "まとめた配布が見つかりません");
      if (batch.state !== "stalled" || !batch.stalledFrom) throw new ConflictError("not_stalled", "まとめた配布は止まっていません");
      if (choice === "reject") {
        if (batch.mainMerged) throw new ConflictError("release_after_main", "main へマージした配布は差し戻せません");
        const next: ReleaseBatch = { ...structuredClone(batch), state: "rejected" };
        if (await this.end(root, batch, next, "rejected", [], this.logRow(root, "resolved", seq, { choice }))) {
          await this.tick(ctx.accountId, repositoryId);
          return { seq, state: "rejected" as const };
        }
        continue;
      }
      const run = await table.getRun(batch.releaseRunId);
      if (!run) throw new NotFoundError("release_not_found", "まとめた配布が見つかりません");
      const started = batch.mainMerged || (await this.releaseStarted(run, batch, reg, batch.stalledFrom === "confirmed"));
      const step = resumeStepOf(batch, started);
      const next: ReleaseBatch = { ...structuredClone(batch), state: batch.stalledFrom, stalledFrom: null, stallReason: null, updatedAt: this.iso() };
      // 状態の戻しと再開のジョブの作成を同じトランザクションで書く（間に見回りが止まった印を書き戻さない）
      const ok = await control.resumeRelease(run, step, {
        root: { ...root, version: root.version + 1, updatedAt: this.iso() },
        expectVersion: root.version,
        batch: next,
        expectBatchState: ["stalled"],
        log: this.logRow(root, "resolved", seq, { choice, step }),
      });
      if (ok) return { seq, state: next.state };
      const now = await table.getRun(batch.releaseRunId);
      if (!now || now.state !== "awaiting_approval" || now.awaitingReason !== "release_stalled" || now.currentJobId !== null) {
        throw new ConflictError("not_stalled", "配布の実行は止まっていません");
      }
    }
    throw new ServiceUnavailableError("organizer_busy");
  }

  // ---- ランナー向け ----

  // そのジョブの実行のレーンの依頼の答えと、リポジトリの配布済みの範囲（#2436 の答えと範囲の形）
  async answersFor(run: SprintRun) {
    const { table } = this.deps;
    const requests = (await table.listOrgRequests(run.accountId, run.repositoryId)).filter((r) =>
      run.loopId ? r.loopId === run.loopId : r.sprintRunId === run.sprintRunId,
    );
    const batches = await table.listBatches(run.accountId, run.repositoryId, 100);
    return {
      answers: requests
        .filter((r) => r.answer !== null)
        .map((r) => ({ request_id: r.requestId, result: r.answer!.result, version: r.answer!.version, release: r.answer!.prodPrUrl, reason: r.answer!.reason, at: r.answer!.at })),
      ranges: batches
        .filter((b) => b.state === "released" && b.result)
        .map((b) => ({ seq: b.seq, sha: b.result!.sha, version: b.result!.version, prs: b.result!.prs, lanes: b.lanes.map((l) => l.lane) })),
    };
  }

  // 配布の実行のまとめた配布の中身（batch.json）
  async batchFor(run: SprintRun, step: ReleaseStep | null) {
    const { table } = this.deps;
    if (run.purpose !== "release" || !run.releaseSeq) return null;
    const batch = await table.getBatch(run.accountId, run.repositoryId, run.releaseSeq);
    if (!batch) return null;
    const requests = await table.listOrgRequests(run.accountId, run.repositoryId);
    return {
      seq: batch.seq,
      step: step ?? "present",
      lanes: batch.lanes.map((l) => ({
        lane: l.lane,
        requests: l.requestIds.map((id) => ({ id, prs: requests.find((r) => r.requestId === id)?.prs ?? [] })),
        issues: l.issues,
      })),
      presentations: batch.presentations.map((p) => ({ k: p.k, sha: p.sha, version: p.version, prs: p.prs, lanes: p.lanes, added_prs: p.addedPrs, at: p.at })),
      choices: choicesOf(batch),
    };
  }

  // ---- 見回り（F8） ----

  async sweep(): Promise<void> {
    for (const { accountId, repositoryId } of await this.deps.table.listOrganizerActive()) {
      await this.tick(accountId, repositoryId).catch((err: Error) =>
        this.log({ event: "organizer_tick_failed", account_id: accountId, repository_id: repositoryId, error: err.name }),
      );
    }
  }
}
