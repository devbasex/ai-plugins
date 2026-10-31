// テストで使う偽の実行の表。確かめと書き込みを 1 つの同期の処理で行い、条件つきの書き込みを再現する
import { isTerminalJob, isTerminalRun, type CredentialRow, type FailureReason, type Registration, type RunJob, type RunState, type Slots, type SprintRun } from "./model.ts";
import type { RunsTable } from "./table.ts";

const regKey = (a: string, r: number) => `${a}#${r}`;

export class MemoryRunsTable implements RunsTable {
  readonly registrations = new Map<string, Registration>();
  readonly credentials = new Map<string, CredentialRow>();
  readonly accountSlots = new Map<string, number>();
  readonly repositorySlots = new Map<string, number>();
  readonly runs = new Map<string, SprintRun>();
  readonly jobs = new Map<string, RunJob>();

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
    return {
      account: this.accountSlots.get(accountId) ?? 0,
      repository: this.repositorySlots.get(regKey(accountId, repositoryId)) ?? 0,
    };
  }

  async createRun(run: SprintRun, job: RunJob, expected: Slots) {
    await this.tick();
    const repoKey = regKey(run.accountId, run.repositoryId);
    if ((this.accountSlots.get(run.accountId) ?? 0) !== expected.account) return false;
    if ((this.repositorySlots.get(repoKey) ?? 0) !== expected.repository) return false;
    this.accountSlots.set(run.accountId, expected.account + 1);
    this.repositorySlots.set(repoKey, expected.repository + 1);
    this.runs.set(run.sprintRunId, structuredClone(run));
    this.jobs.set(job.jobId, structuredClone(job));
    return true;
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

  async addResumeJob(job: RunJob, now: string) {
    await this.tick();
    const r = this.runs.get(job.sprintRunId);
    if (!r || r.state !== "awaiting_approval" || r.currentJobId !== null) return false;
    Object.assign(r, { state: "starting", currentJobId: job.jobId, jobCount: job.seq, updatedAt: now });
    this.jobs.set(job.jobId, structuredClone(job));
    return true;
  }

  async awaitApproval(sprintRunId: string, jobId: string, now: string) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (!r || isTerminalRun(r.state) || r.currentJobId !== jobId) return false;
    Object.assign(r, { state: "awaiting_approval", currentJobId: null, updatedAt: now });
    return true;
  }

  async finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string) {
    await this.tick();
    const r = this.runs.get(sprintRunId);
    if (!r || isTerminalRun(r.state)) return false;
    Object.assign(r, { state, failureReason: reason, currentJobId: null, updatedAt: now });
    const repoKey = regKey(r.accountId, r.repositoryId);
    this.accountSlots.set(r.accountId, (this.accountSlots.get(r.accountId) ?? 0) - 1);
    this.repositorySlots.set(repoKey, (this.repositorySlots.get(repoKey) ?? 0) - 1);
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
    Object.assign(j, structuredClone(patch));
    return true;
  }
}
