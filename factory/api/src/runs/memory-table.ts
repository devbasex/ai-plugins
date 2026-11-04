// テストで使う偽の実行の表。確かめと書き込みを 1 つの同期の処理で行い、条件つきの書き込みとトランザクションを再現する
import type { OrganizerRoot, OrganizerWrite, OrgLog, OrgRequest, ReleaseAnswer, ReleaseBatch } from "../organizer/model.ts";
import {
  isTerminalJob,
  isTerminalLoop,
  isTerminalRun,
  type CreateRunGuards,
  type CreateRunResult,
  type CredentialRow,
  type DevLoop,
  type FailureReason,
  type LoopWrite,
  type Registration,
  type RunJob,
  type RunState,
  type Slots,
  type SprintRun,
} from "./model.ts";
import type { ComposeRelease, ResumeOptions, RunsTable, RunTransitionExtras } from "./table.ts";
import { epochSeconds, idempotencyPk, liveCreatedId, type IdempotencyClaim, type IdempotencyRef } from "../idempotency.ts";

const regKey = (a: string, r: number) => `${a}#${r}`;
const shareKey = (a: string, r: number, k: string | number) => `${a}#${r}#${k}`;

export class MemoryRunsTable implements RunsTable {
  readonly registrations = new Map<string, Registration>();
  readonly credentials = new Map<string, CredentialRow>();
  readonly accountSlots = new Map<string, number>();
  readonly repositorySlots = new Map<string, number>();
  readonly legacySlots = new Map<string, number>();
  readonly runs = new Map<string, SprintRun>();
  readonly jobs = new Map<string, RunJob>();
  readonly loops = new Map<string, DevLoop>();
  // <account>#<repo>#<題> → 占めるループ
  readonly milestoneShares = new Map<string, { loopId: string; title: string }>();
  // <account>#<repo>#<番号> → 占める実行
  readonly issueShares = new Map<string, string>();
  // <account>#<runnerId> → running の local のジョブの数
  readonly runnerSlots = new Map<string, number>();
  // <account>#<repo> → 古い版の実行を数えに入れた回数（#2439 の I62）
  readonly legacySeqs = new Map<string, number>();
  // 作成の冪等の鍵の行（IDEMP#<利用者>#<操作>#<鍵>）。TTL で消さない
  readonly idempotency = new Map<string, IdempotencyClaim>();
  // Factory のオーガナイザー（#2439）。<account>#<repo>（#<id|seq>）→ 行
  readonly organizers = new Map<string, OrganizerRoot>();
  readonly orgRequests = new Map<string, OrgRequest>();
  readonly batches = new Map<string, ReleaseBatch>();
  readonly orgLogs: OrgLog[] = [];
  // テストで、次の composeRelease のトランザクションを落とす
  failNextCompose = false;
  migrated = false;

  private async tick(): Promise<void> {
    await Promise.resolve();
  }

  async getRegistration(accountId: string, repositoryId: number) {
    await this.tick();
    return structuredClone(this.registrations.get(regKey(accountId, repositoryId)) ?? null);
  }

  async listRegistrations(accountId: string) {
    await this.tick();
    return [...this.registrations.values()].filter((r) => r.accountId === accountId).map((r) => structuredClone(r));
  }

  async putRegistration(registration: Registration) {
    await this.tick();
    const key = regKey(registration.accountId, registration.repositoryId);
    if (this.registrations.has(key)) return false;
    this.registrations.set(key, structuredClone(registration));
    return true;
  }

  async setRegistrationName(accountId: string, repositoryId: number, fullName: string) {
    await this.tick();
    const r = this.registrations.get(regKey(accountId, repositoryId));
    if (r) r.fullName = fullName;
  }

  async setMaxConcurrentRuns(accountId: string, repositoryId: number, value: number | null) {
    await this.tick();
    const r = this.registrations.get(regKey(accountId, repositoryId));
    if (!r) return false;
    r.maxConcurrentRuns = value;
    return true;
  }

  async deleteRegistration(accountId: string, repositoryId: number) {
    await this.tick();
    const key = regKey(accountId, repositoryId);
    if (!this.registrations.has(key)) return "not_found" as const;
    if ((this.repositorySlots.get(key) ?? 0) > 0) return "in_use" as const;
    this.registrations.delete(key);
    return "deleted" as const;
  }

  async getCredential(accountId: string) {
    await this.tick();
    return structuredClone(this.credentials.get(accountId) ?? null);
  }

  async putCredential(row: CredentialRow) {
    await this.tick();
    this.credentials.set(row.accountId, structuredClone(row));
  }

  async deleteCredential(accountId: string) {
    await this.tick();
    this.credentials.delete(accountId);
  }

  async getSlots(accountId: string, repositoryId: number): Promise<Slots> {
    await this.tick();
    const key = regKey(accountId, repositoryId);
    const legacy = this.legacySlots.get(key) ?? 0;
    const legacySeq = this.legacySeqs.get(key);
    return {
      account: this.accountSlots.get(accountId) ?? 0,
      repository: this.repositorySlots.get(key) ?? 0,
      ...(legacy !== 0 ? { legacy } : {}),
      ...(legacySeq !== undefined ? { legacySeq } : {}),
    };
  }

  // ループの書き込みの条件
  private loopFits(write: LoopWrite): boolean {
    const loop = this.loops.get(write.loopId);
    if (!loop || loop.accountId !== write.accountId) return false;
    const e = write.expect;
    if (e.currentRunId !== undefined && loop.currentRunId !== e.currentRunId) return false;
    if (e.lapRuns !== undefined && loop.lapRuns !== e.lapRuns) return false;
    if (e.states && !e.states.includes(loop.state)) return false;
    if (e.answers !== undefined && JSON.stringify(loop.answers) !== JSON.stringify(e.answers)) return false;
    return true;
  }

  private applyLoop(write: LoopWrite): void {
    const loop = this.loops.get(write.loopId)!;
    Object.assign(loop, structuredClone(write.set));
    if (write.releaseMilestones) {
      for (const title of write.releaseMilestones.titles) {
        const key = shareKey(write.accountId, write.releaseMilestones.repositoryId, title);
        if (this.milestoneShares.get(key)?.loopId === write.loopId) this.milestoneShares.delete(key);
      }
    }
  }

  async createRun(run: SprintRun, job: RunJob, expected: Slots, guards?: CreateRunGuards): Promise<CreateRunResult> {
    await this.tick();
    const repoKey = regKey(run.accountId, run.repositoryId);
    if ((this.accountSlots.get(run.accountId) ?? 0) !== expected.account) return "slots";
    if ((this.repositorySlots.get(repoKey) ?? 0) !== expected.repository) return "slots";
    if (guards) {
      if (!guards.legacy && (this.legacySlots.get(repoKey) ?? 0) !== 0) return "legacy";
      // 古い版の実行は、まとめた配布が流れている間は始めない（#2439 の I62）
      if (guards.legacy && (this.organizers.get(repoKey)?.currentSeq ?? null) !== null) return "release_running";
      if (guards.issues.some((n) => this.issueShares.has(shareKey(run.accountId, run.repositoryId, n)))) return "issue_in_use";
      if (guards.milestoneChecks.some((t) => this.milestoneShares.has(shareKey(run.accountId, run.repositoryId, t)))) return "issue_in_loop";
      if (guards.loop && !this.loopFits(guards.loop)) return "loop_changed";
      if (guards.idempotency && !this.idempotencyFree(guards.idempotency)) return "duplicate";
    }
    this.accountSlots.set(run.accountId, expected.account + 1);
    this.repositorySlots.set(repoKey, expected.repository + 1);
    if (guards) {
      if (guards.legacy) {
        this.legacySlots.set(repoKey, (this.legacySlots.get(repoKey) ?? 0) + 1);
        this.legacySeqs.set(repoKey, (this.legacySeqs.get(repoKey) ?? 0) + 1);
      }
      for (const n of guards.issues) this.issueShares.set(shareKey(run.accountId, run.repositoryId, n), run.sprintRunId);
      if (guards.loop) this.applyLoop(guards.loop);
      if (guards.idempotency) this.idempotency.set(idempotencyPk(guards.idempotency), { ...guards.idempotency });
    }
    this.runs.set(run.sprintRunId, structuredClone({ ...run, ...(guards ? { legacy: guards.legacy } : {}) }));
    this.jobs.set(job.jobId, structuredClone(job));
    return "created";
  }

  async getRun(sprintRunId: string) {
    await this.tick();
    return structuredClone(this.runs.get(sprintRunId) ?? null);
  }

  async listRuns(accountId: string, limit: number) {
    await this.tick();
    return [...this.runs.values()]
      .filter((r) => r.accountId === accountId)
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0))
      .slice(0, limit)
      .map((r) => structuredClone(r));
  }

  async setCancelRequested(sprintRunId: string) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (r) r.cancelRequested = true;
  }

  async markRunRunning(sprintRunId: string, now: string) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (r && r.state === "starting") Object.assign(r, { state: "running", updatedAt: now });
  }

  async addResumeJob(job: RunJob, now: string, opts: ResumeOptions = {}) {
    await this.tick();
    const r = this.runs.get(job.sprintRunId);
    if (!r || r.state !== (opts.from ?? "awaiting_approval") || r.currentJobId !== null) return false;
    const expect = opts.expectAwaiting;
    if (expect) {
      if (r.awaitingReason !== expect.reason) return false;
      if (expect.releaseRequestId !== undefined && r.releaseRequestId !== expect.releaseRequestId) return false;
    } else if ((r.awaitingReason ?? null) !== null) {
      return false;
    }
    if (opts.loop && !this.loopFits(opts.loop)) return false;
    if (opts.organizer && !this.organizerFits(opts.organizer)) return false;
    Object.assign(r, { state: "starting", currentJobId: job.jobId, jobCount: job.seq, updatedAt: now, resumeAt: null, awaitingReason: null, releaseRequestId: null });
    this.jobs.set(job.jobId, structuredClone(job));
    if (opts.loop) this.applyLoop(opts.loop);
    if (opts.organizer) this.applyOrganizer(opts.organizer);
    return true;
  }

  async awaitApproval(sprintRunId: string, jobId: string, now: string, extras: RunTransitionExtras = {}) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (!r || isTerminalRun(r.state) || r.currentJobId !== jobId) return false;
    if (extras.loop && !this.loopFits(extras.loop)) return false;
    Object.assign(r, {
      state: "awaiting_approval",
      currentJobId: null,
      updatedAt: now,
      awaitingReason: extras.awaitingReason ?? null,
      releaseRequestId: extras.releaseRequestId ?? null,
    });
    if (extras.clearCancel) r.cancelRequested = false;
    if (extras.finishReason !== undefined) r.finishReason = extras.finishReason;
    if (extras.loop) this.applyLoop(extras.loop);
    this.putRequests(extras.requests);
    return true;
  }

  async awaitReset(sprintRunId: string, jobId: string, resumeAt: string, now: string, extras: RunTransitionExtras = {}) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (!r || isTerminalRun(r.state) || r.currentJobId !== jobId) return false;
    if (extras.loop && !this.loopFits(extras.loop)) return false;
    Object.assign(r, { state: "awaiting_reset", currentJobId: null, resumeAt, finishReason: extras.finishReason ?? "usage_limit", updatedAt: now });
    if (extras.loop) this.applyLoop(extras.loop);
    return true;
  }

  async listResetRuns(before: string) {
    await this.tick();
    return [...this.runs.values()]
      .filter((r) => r.state === "awaiting_reset" && r.resumeAt && r.resumeAt <= before)
      .sort((a, b) => ((a.resumeAt ?? "") < (b.resumeAt ?? "") ? -1 : 1))
      .map((r) => structuredClone(r));
  }

  async finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string, extras: RunTransitionExtras = {}) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (!r || isTerminalRun(r.state)) return false;
    if (extras.loop && !this.loopFits(extras.loop)) return false;
    Object.assign(r, { state, failureReason: reason, currentJobId: null, updatedAt: now, resumeAt: null, awaitingReason: null, releaseRequestId: null });
    if (extras.finishReason !== undefined) r.finishReason = extras.finishReason;
    const repoKey = regKey(r.accountId, r.repositoryId);
    // 配布の実行は枠を持たない（#2439 の I64）
    if (r.purpose !== "release") {
      this.accountSlots.set(r.accountId, (this.accountSlots.get(r.accountId) ?? 0) - 1);
      this.repositorySlots.set(repoKey, (this.repositorySlots.get(repoKey) ?? 0) - 1);
      if (r.legacy === true) this.legacySlots.set(repoKey, (this.legacySlots.get(repoKey) ?? 0) - 1);
    }
    for (const [key, owner] of this.issueShares) if (owner === sprintRunId) this.issueShares.delete(key);
    if (extras.loop) this.applyLoop(extras.loop);
    this.putRequests(extras.requests);
    return true;
  }

  async getJob(jobId: string) {
    await this.tick();
    return structuredClone(this.jobs.get(jobId) ?? null);
  }

  async listActiveJobs() {
    await this.tick();
    return [...this.jobs.values()]
      .filter((j) => !isTerminalJob(j.state))
      .sort((a, b) => (a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0))
      .map((j) => structuredClone(j));
  }

  async updateJob(jobId: string, patch: Partial<RunJob>, from?: RunJob["state"][]) {
    await this.tick();
    const j = this.jobs.get(jobId);
    if (!j || (from && !from.includes(j.state))) return false;
    const leaving = patch.state !== undefined && isTerminalJob(patch.state) && !isTerminalJob(j.state);
    if (leaving && j.runnerId && j.state === "running") {
      const key = `${j.accountId}#${j.runnerId}`;
      this.runnerSlots.set(key, Math.max(0, (this.runnerSlots.get(key) ?? 0) - 1));
    }
    Object.assign(j, structuredClone(patch));
    return true;
  }

  async claimLocalJob(jobId: string, runnerId: string, maxJobs: number, now: string) {
    await this.tick();
    const j = this.jobs.get(jobId);
    if (!j || j.state !== "pending" || j.runnerKind !== "local") return false;
    const key = `${j.accountId}#${runnerId}`;
    const count = this.runnerSlots.get(key) ?? 0;
    if (count >= maxJobs) return false;
    this.runnerSlots.set(key, count + 1);
    Object.assign(j, { state: "running", startedAt: now, runnerId });
    return true;
  }

  // 期限の中の同じ鍵の行が無い（DynamoDB の attribute_not_exists(pk) OR ttl < :now と同じ）
  private idempotencyFree(claim: IdempotencyClaim): boolean {
    const row = this.idempotency.get(idempotencyPk(claim));
    return !row || row.ttl < epochSeconds(claim.createdAt);
  }

  async getIdempotent(ref: IdempotencyRef, now: string) {
    await this.tick();
    return liveCreatedId(this.idempotency.get(idempotencyPk(ref)) ?? null, now);
  }

  async createLoop(loop: DevLoop, idempotency?: IdempotencyClaim) {
    await this.tick();
    for (const title of loop.milestones) {
      if (this.milestoneShares.has(shareKey(loop.accountId, loop.repositoryId, title))) return { conflict: title };
    }
    if (idempotency && !this.idempotencyFree(idempotency)) return "duplicate" as const;
    if (idempotency) this.idempotency.set(idempotencyPk(idempotency), { ...idempotency });
    for (const title of loop.milestones) this.milestoneShares.set(shareKey(loop.accountId, loop.repositoryId, title), { loopId: loop.loopId, title });
    this.loops.set(loop.loopId, structuredClone(loop));
    return "created" as const;
  }

  async getLoop(accountId: string, loopId: string) {
    await this.tick();
    const loop = this.loops.get(loopId);
    return loop && loop.accountId === accountId ? structuredClone(loop) : null;
  }

  async listLoops(accountId: string) {
    await this.tick();
    return [...this.loops.values()]
      .filter((l) => l.accountId === accountId)
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0))
      .map((l) => structuredClone(l));
  }

  async listLoopRuns(accountId: string, loopId: string) {
    await this.tick();
    return [...this.runs.values()].filter((r) => r.accountId === accountId && r.loopId === loopId).map((r) => structuredClone(r));
  }

  async writeLoop(write: LoopWrite) {
    await this.tick();
    if (!this.loopFits(write)) return false;
    this.applyLoop(write);
    return true;
  }

  async getMilestoneShare(accountId: string, repositoryId: number, title: string) {
    await this.tick();
    return structuredClone(this.milestoneShares.get(shareKey(accountId, repositoryId, title)) ?? null);
  }

  async listIssueShares(accountId: string, repositoryId: number) {
    await this.tick();
    const prefix = `${accountId}#${repositoryId}#`;
    return [...this.issueShares.entries()]
      .filter(([key]) => key.startsWith(prefix))
      .map(([key, sprintRunId]) => ({ number: Number(key.slice(prefix.length)), sprintRunId }))
      .sort((a, b) => a.number - b.number);
  }

  async listWaitingLoops(accountId?: string) {
    await this.tick();
    return [...this.loops.values()]
      .filter((l) => accountId === undefined || l.accountId === accountId)
      .filter((l) => l.state === "between_laps" && l.waitReason !== "user_start" && !isTerminalLoop(l.state))
      .sort((a, b) => (a.updatedAt < b.updatedAt ? -1 : 1))
      .map((l) => structuredClone(l));
  }

  async isMigrated() {
    await this.tick();
    return this.migrated;
  }

  async setMigrated() {
    await this.tick();
    this.migrated = true;
  }

  async listUnbackfilledRuns() {
    await this.tick();
    return [...this.runs.values()].filter((r) => r.legacy === undefined && !isTerminalRun(r.state)).map((r) => structuredClone(r));
  }

  async backfillRun(run: SprintRun, issues: number[]) {
    await this.tick();
    const r = this.runs.get(run.sprintRunId);
    if (!r || r.legacy !== undefined || isTerminalRun(r.state)) return false;
    if (issues.some((n) => this.issueShares.has(shareKey(r.accountId, r.repositoryId, n)))) return false;
    r.legacy = true;
    const repoKey = regKey(r.accountId, r.repositoryId);
    this.legacySlots.set(repoKey, (this.legacySlots.get(repoKey) ?? 0) + 1);
    this.legacySeqs.set(repoKey, (this.legacySeqs.get(repoKey) ?? 0) + 1);
    for (const n of issues) this.issueShares.set(shareKey(r.accountId, r.repositoryId, n), r.sprintRunId);
    return true;
  }

  // ---- Factory のオーガナイザー（#2439） ----

  private putRequests(requests: OrgRequest[] | undefined): void {
    for (const req of requests ?? []) {
      const key = shareKey(req.accountId, req.repositoryId, req.requestId);
      if (!this.orgRequests.has(key)) this.orgRequests.set(key, structuredClone(req));
    }
  }

  private organizerFits(write: OrganizerWrite): boolean {
    const key = regKey(write.root.accountId, write.root.repositoryId);
    const current = this.organizers.get(key);
    if (write.expectVersion === null ? current !== undefined : current?.version !== write.expectVersion) return false;
    if (write.expectBatchState && write.batch) {
      const b = this.batches.get(shareKey(write.batch.accountId, write.batch.repositoryId, write.batch.seq));
      if (!b || !write.expectBatchState.includes(b.state)) return false;
    }
    return true;
  }

  private applyOrganizer(write: OrganizerWrite): void {
    this.organizers.set(regKey(write.root.accountId, write.root.repositoryId), structuredClone(write.root));
    if (write.batch) this.batches.set(shareKey(write.batch.accountId, write.batch.repositoryId, write.batch.seq), structuredClone(write.batch));
    if (write.log) this.orgLogs.push(structuredClone(write.log));
  }

  async getOrganizer(accountId: string, repositoryId: number) {
    await this.tick();
    return structuredClone(this.organizers.get(regKey(accountId, repositoryId)) ?? null);
  }

  async writeOrganizer(write: OrganizerWrite) {
    await this.tick();
    if (!this.organizerFits(write)) return false;
    this.applyOrganizer(write);
    return true;
  }

  async composeRelease(input: ComposeRelease) {
    await this.tick();
    if (this.failNextCompose) {
      this.failNextCompose = false;
      throw new Error("TransactionCanceled");
    }
    const { write, run, job } = input;
    const key = regKey(write.root.accountId, write.root.repositoryId);
    if (!this.organizerFits(write)) return false;
    const current = this.organizers.get(key);
    if (current && current.currentSeq !== null) return false;
    if ((this.legacySeqs.get(key) ?? null) !== input.legacySeq) return false;
    if (this.batches.has(shareKey(write.batch.accountId, write.batch.repositoryId, write.batch.seq))) return false;
    this.applyOrganizer(write);
    this.runs.set(run.sprintRunId, structuredClone(run));
    this.jobs.set(job.jobId, structuredClone(job));
    return true;
  }

  async listOrgRequests(accountId: string, repositoryId: number) {
    await this.tick();
    return [...this.orgRequests.values()]
      .filter((r) => r.accountId === accountId && r.repositoryId === repositoryId)
      .sort((a, b) => (a.requestedAt < b.requestedAt ? -1 : a.requestedAt > b.requestedAt ? 1 : 0))
      .map((r) => structuredClone(r));
  }

  async answerRequest(accountId: string, repositoryId: number, requestId: string, answer: ReleaseAnswer) {
    await this.tick();
    const r = this.orgRequests.get(shareKey(accountId, repositoryId, requestId));
    if (!r || r.answer !== null) return false;
    r.answer = structuredClone(answer);
    return true;
  }

  async markDelivered(accountId: string, repositoryId: number, requestId: string, at: string) {
    await this.tick();
    const r = this.orgRequests.get(shareKey(accountId, repositoryId, requestId));
    if (r && r.deliveredAt === null) r.deliveredAt = at;
  }

  async getBatch(accountId: string, repositoryId: number, seq: number) {
    await this.tick();
    return structuredClone(this.batches.get(shareKey(accountId, repositoryId, seq)) ?? null);
  }

  async listBatches(accountId: string, repositoryId: number, limit: number) {
    await this.tick();
    return [...this.batches.values()]
      .filter((b) => b.accountId === accountId && b.repositoryId === repositoryId)
      .sort((a, b) => b.seq - a.seq)
      .slice(0, limit)
      .map((b) => structuredClone(b));
  }

  async appendOrgLog(log: OrgLog) {
    await this.tick();
    this.orgLogs.push(structuredClone(log));
  }

  async listOrgLogs(accountId: string, repositoryId: number) {
    await this.tick();
    return this.orgLogs.filter((l) => l.accountId === accountId && l.repositoryId === repositoryId).map((l) => structuredClone(l));
  }

  async listOrganizerActive() {
    await this.tick();
    const out = new Map<string, { accountId: string; repositoryId: number }>();
    for (const root of this.organizers.values()) {
      if (root.currentSeq !== null) out.set(regKey(root.accountId, root.repositoryId), { accountId: root.accountId, repositoryId: root.repositoryId });
    }
    for (const req of this.orgRequests.values()) {
      if (req.deliveredAt === null) out.set(regKey(req.accountId, req.repositoryId), { accountId: req.accountId, repositoryId: req.repositoryId });
    }
    return [...out.values()];
  }
}
