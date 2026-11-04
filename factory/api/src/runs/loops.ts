// ループの制御（#2347 の F1〜F6）。開発ループの集約の持ち主で、始める・止める・予算・答え・次の周回の操作、
// 周回の開始（startNextLap）、待っているループを起こす処理、停止の後の処理から呼ばれるループの状態の写し（決定 4）を持つ。
// 課題の選定の規則は持たない（ランナーの中の NDF の開発ループが行う）。常駐のプロセスは持たず、API と見回りの中で動く（決定 1）
import { randomUUID } from "node:crypto";
import type { LoopLapClosedPayload, LoopStoppedPayload } from "@ndf-factory/contracts";
import type { AccountContext, AccountRecord, SeatRecord } from "../accounts/model.ts";
import { ApiError, ConflictError, InvalidRequestError, NotFoundError } from "../errors.ts";
import type { ProgressReader } from "../progress/read.ts";
import type { LoopHooks, RunControl } from "./control.ts";
import {
  isTerminalLoop,
  type DevLoop,
  type FailureReason,
  type FinishReason,
  type LoopAnswer,
  type LoopBudget,
  type LoopContext,
  type LoopPending,
  type LoopWrite,
  type RunState,
  type SprintRun,
  type WaitReason,
} from "./model.ts";
import type { RunsTable } from "./table.ts";

export const LOOP_NAME = /^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$/;
export const MAX_MILESTONES = 10;
export const MAX_MILESTONE_LENGTH = 100;
const LIST_LIMIT = 50;
const START_RETRIES = 3;

// 実行の終わり方と、そのジョブが送った loop.* の組（決定 4 の表の入力）
export interface RunOutcome {
  runState: Exclude<RunState, "starting" | "running">;
  failureReason: FailureReason | null;
  finishReason: FinishReason | null;
  laps: LoopLapClosedPayload[];
  stopped: LoopStoppedPayload | null;
}

// 決定 4 の表。実行の終わり方と loop.stopped の組からだけループの状態を決め、表に無い組は stopped（run_failed）にする（I53）
export function loopStateAfter(o: RunOutcome, cancelRequested: boolean): { state: DevLoop["state"]; condition: string | null; detail: string | null } {
  const cond = o.stopped?.condition ?? null;
  const unknown = { state: "stopped" as const, condition: "run_failed", detail: JSON.stringify({ run_state: o.runState, condition: cond }) };
  if (o.runState === "cancelled" || (cancelRequested && o.runState !== "awaiting_reset" && o.runState !== "awaiting_approval")) {
    return { state: "cancelled", condition: null, detail: null };
  }
  if (o.runState === "awaiting_approval") return cond === null || cond === "gate2_returned" ? { state: "awaiting_approval", condition: null, detail: null } : unknown;
  if (o.runState === "awaiting_reset") return { state: "running", condition: null, detail: null };
  if ((o.runState === "done" || o.runState === "stopped") && cond !== null) {
    return cond === "gate2_returned" ? unknown : { state: "stopped", condition: cond, detail: o.stopped!.detail };
  }
  if (o.runState === "done") return { state: "between_laps", condition: null, detail: null };
  if (o.runState === "failed" && cond === null) return { state: "stopped", condition: "run_failed", detail: o.failureReason };
  if (o.runState === "stopped") return { state: "stopped", condition: "run_stopped", detail: o.finishReason };
  return unknown;
}

// 停止の後の処理で、実行の終わりと同じトランザクションへ入れるループの書き込み
export function loopWriteAfterRun(loop: DevLoop, run: SprintRun, o: RunOutcome, now: string): LoopWrite {
  const next = loopStateAfter(o, loop.cancelRequested);
  const terminal = isTerminalLoop(next.state);
  // 人の判断待ちは最後に届いた写し（ランナーは答えの無いものをすべて送る）。答えは書き換えない（同時に保存された答えを
  // 古い配列で消さないため）。写しに残らない番号の答えは、指示へ載せるときと次の答えの保存のときに落とす
  const lastPending = o.stopped?.pending ?? o.laps.at(-1)?.pending ?? null;
  const pending: LoopPending[] = lastPending ? lastPending.map((p) => ({ number: p.number, reason: p.reason, lap: p.lap })) : loop.pending;
  const runEnded = o.runState !== "awaiting_approval" && o.runState !== "awaiting_reset";
  const set: LoopWrite["set"] = {
    state: next.state,
    waitReason: null,
    lapsClosed: loop.lapsClosed + o.laps.length,
    closedCostUsd: round(loop.closedCostUsd + o.laps.reduce((s, l) => s + l.cost_usd, 0)),
    closedSeconds: round(loop.closedSeconds + o.laps.reduce((s, l) => s + l.seconds, 0)),
    pending,
    lastRunAt: now,
    updatedAt: now,
    ...(runEnded ? { currentRunId: null } : {}),
    ...(next.condition !== null ? { stopCondition: next.condition, stopDetail: next.detail } : {}),
  };
  return {
    accountId: loop.accountId,
    loopId: loop.loopId,
    set,
    expect: { currentRunId: run.sprintRunId },
    ...(terminal ? { releaseMilestones: { repositoryId: loop.repositoryId, titles: loop.milestones } } : {}),
  };
}

// 承認の答えで実行が再開するとき、ループを running へ戻す
export function loopWriteOnResume(loop: DevLoop, run: SprintRun, now: string): LoopWrite {
  return {
    accountId: loop.accountId,
    loopId: loop.loopId,
    set: { state: "running", waitReason: null, updatedAt: now },
    expect: { currentRunId: run.sprintRunId, states: ["awaiting_approval"] },
  };
}

const round = (n: number) => Math.round(n * 1e6) / 1e6;

// ループを始めた利用者の今の文脈を引く。席を外された・アカウントが無ければ null（周回を起こさない）
export type ContextResolver = (accountId: string, userId: string) => Promise<AccountContext | null>;

// アカウントの行と席から文脈を組む（resolveAccount と同じ規則。要求の無い見回りで、保存した写しを信じないため）
export function accountContextResolver(accounts: {
  getAccount(accountId: string): Promise<AccountRecord | null>;
  listSeats(ctx: AccountContext): Promise<SeatRecord[]>;
}): ContextResolver {
  return async (accountId, userId) => {
    const account = await accounts.getAccount(accountId);
    if (!account) return null;
    const base = { accountId, userId, kind: account.kind, plan: account.plan, executionMode: account.executionMode };
    const role = account.ownerUserId === userId ? ("owner" as const) : ("member" as const);
    if (account.kind === "personal") return account.ownerUserId === userId ? { ...base, role, seatType: null } : null;
    const seat = (await accounts.listSeats({ ...base, role, seatType: null })).find((x) => x.userId === userId);
    return seat ? { ...base, role, seatType: seat.seatType } : null;
  };
}

export interface LoopControlDeps {
  table: RunsTable;
  control: RunControl;
  reader: ProgressReader;
  // 周回を起こすたびに、始めた利用者の今の文脈を引き直す。無いときは保存した写しを使う
  resolveContext?: ContextResolver;
  now?: () => Date;
  newId?: () => string;
  log?: (entry: Record<string, unknown>) => void;
}

const contextOf = (ctx: AccountContext): LoopContext => ({
  userId: ctx.userId,
  plan: ctx.plan,
  seatType: ctx.seatType,
  executionMode: ctx.executionMode,
  kind: ctx.kind,
  role: ctx.role,
});

function parseBudget(raw: unknown): LoopBudget {
  if (raw === undefined || raw === null) return { laps: null, usd: null, hours: null };
  if (typeof raw !== "object" || Array.isArray(raw)) throw new InvalidRequestError("予算の形ではありません");
  const out: LoopBudget = { laps: null, usd: null, hours: null };
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (k !== "laps" && k !== "usd" && k !== "hours") throw new InvalidRequestError("予算は laps・usd・hours だけです");
    if (v === null || v === undefined) continue;
    if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) throw new InvalidRequestError("予算は 0 より大きい数にしてください");
    out[k] = v;
  }
  return out;
}

export class LoopControl {
  private readonly now: () => Date;
  private readonly newId: () => string;
  private readonly log: (entry: Record<string, unknown>) => void;

  constructor(private readonly deps: LoopControlDeps) {
    this.now = deps.now ?? (() => new Date());
    this.newId = deps.newId ?? (() => randomUUID().replaceAll("-", ""));
    this.log = deps.log ?? ((entry) => console.log(JSON.stringify(entry)));
  }

  private iso(): string {
    return this.now().toISOString();
  }

  // 実行の制御へ差し込む口
  hooks(): LoopHooks {
    return {
      startNextLap: (accountId, loopId) => this.startNextLap(accountId, loopId, false),
      wakeWaitingLoops: (accountId) => this.wakeWaitingLoops(accountId),
      sweepLoops: () => this.sweepLoops(),
    };
  }

  private async loopOf(ctx: AccountContext, loopId: string): Promise<DevLoop> {
    const loop = await this.deps.table.getLoop(ctx.accountId, loopId);
    if (!loop) throw new NotFoundError("loop_not_found", "開発ループが見つかりません");
    return loop;
  }

  // F1: 始める。マイルストーンの持ち分とループの行を 1 回で書き、続けて最初の周回を起こす（枠で待つときも 201）
  async startLoop(ctx: AccountContext, repositoryId: number, body: Record<string, unknown>): Promise<{ loop_id: string; state: DevLoop["state"] }> {
    const { table } = this.deps;
    const name = body.name;
    if (typeof name !== "string" || !LOOP_NAME.test(name)) throw new InvalidRequestError("ループの名前は英数字と - _ で 64 字までにしてください");
    const milestones = body.milestones;
    if (
      !Array.isArray(milestones) ||
      milestones.length < 1 ||
      milestones.length > MAX_MILESTONES ||
      !milestones.every((m) => typeof m === "string" && m.length >= 1 && m.length <= MAX_MILESTONE_LENGTH && !m.includes(",")) ||
      new Set(milestones).size !== milestones.length
    ) {
      throw new InvalidRequestError(`マイルストーンは 1〜${MAX_MILESTONES} 件、各 ${MAX_MILESTONE_LENGTH} 字までで , を含めずに選んでください`);
    }
    const budget = parseBudget(body.budget);
    const registration = await table.getRegistration(ctx.accountId, repositoryId);
    if (!registration) throw new NotFoundError("repository_not_found", "登録したリポジトリが見つかりません");
    const same = (await table.listLoops(ctx.accountId)).find((l) => l.repositoryId === repositoryId && l.name === name && !isTerminalLoop(l.state));
    if (same) throw new ConflictError("loop_name_in_use", `ループ ${name} は終わっていません`);
    const now = this.iso();
    const loop: DevLoop = {
      loopId: this.newId(),
      accountId: ctx.accountId,
      repositoryId,
      name,
      milestones: milestones as string[],
      budget,
      state: "between_laps",
      waitReason: null,
      currentRunId: null,
      lapRuns: 0,
      lapsClosed: 0,
      closedCostUsd: 0,
      closedSeconds: 0,
      stopCondition: null,
      stopDetail: null,
      pending: [],
      answers: [],
      cancelRequested: false,
      context: contextOf(ctx),
      createdBy: ctx.userId,
      createdAt: now,
      updatedAt: now,
      lastRunAt: null,
    };
    const created = await table.createLoop(loop);
    if (created !== "created") {
      const share = await table.getMilestoneShare(ctx.accountId, repositoryId, created.conflict);
      const other = share ? await table.getLoop(ctx.accountId, share.loopId) : null;
      const overlapping = other ? loop.milestones.filter((m) => other.milestones.includes(m)) : [created.conflict];
      throw new ConflictError("milestone_in_use", `${overlapping.join("、")} はループ ${other?.name ?? ""} が選んでいます`, {
        milestones: overlapping,
        loop_name: other?.name ?? null,
      });
    }
    // 始めるのは利用者の操作のため、local でも最初の周回を起こす（I52）
    await this.startNextLap(ctx.accountId, loop.loopId, true);
    const after = await table.getLoop(ctx.accountId, loop.loopId);
    return { loop_id: loop.loopId, state: after?.state ?? loop.state };
  }

  // 周回の開始（決定 2、I44・I52）。between_laps で今の実行が無いループだけを起こす。条件で落ちれば読み直して 3 回まで
  async startNextLap(accountId: string, loopId: string, byUser: boolean): Promise<void> {
    const { table, control } = this.deps;
    for (let attempt = 0; attempt < START_RETRIES; attempt++) {
      const loop = await table.getLoop(accountId, loopId);
      if (!loop || loop.state !== "between_laps" || loop.currentRunId !== null) return;
      const now = this.iso();
      // 停止の後の処理がループを読んだ後に取り消しの要求が保存されると、between_laps のまま要求だけが残る。
      // 周回を起こさずに cancelled へ移し、マイルストーンの持ち分を消す
      if (loop.cancelRequested) {
        const ok = await table.writeLoop({
          accountId: loop.accountId,
          loopId: loop.loopId,
          set: { state: "cancelled", waitReason: null, updatedAt: now },
          expect: { currentRunId: null, lapRuns: loop.lapRuns, states: ["between_laps"] },
          releaseMilestones: { repositoryId: loop.repositoryId, titles: loop.milestones },
        });
        if (ok) return;
        continue;
      }
      // 保存した写しを判定に使わない（料金プランの変更・席の外し・形態の変更を周回ごとに照らす）
      if (this.deps.resolveContext) {
        const fresh = await this.deps.resolveContext(loop.accountId, loop.createdBy);
        if (!fresh) {
          await this.refused(loop, new ConflictError("seat_missing", "ループを始めた利用者がこのアカウントの席を持っていません"));
          return;
        }
        loop.context = contextOf(fresh);
      }
      if (loop.context.executionMode === "local" && !control.config.localLoopAutostart && !byUser) {
        if (loop.waitReason !== "user_start") {
          await this.setWaiting(loop, "user_start");
          this.log({ event: "loop_awaiting_user_start", loop_id: loop.loopId });
        }
        return;
      }
      try {
        const result = await control.startLapRun(loop, (run) => ({
          accountId: loop.accountId,
          loopId: loop.loopId,
          set: {
            state: "running",
            waitReason: null,
            currentRunId: run.sprintRunId,
            lapRuns: loop.lapRuns + 1,
            context: loop.context,
            lastRunAt: now,
            updatedAt: now,
          },
          expect: { currentRunId: null, lapRuns: loop.lapRuns, states: ["between_laps"] },
        }));
        if (result === "loop_changed") continue;
        return;
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        await this.refused(loop, err);
        return;
      }
    }
  }

  private async setWaiting(loop: DevLoop, reason: WaitReason): Promise<boolean> {
    return this.deps.table.writeLoop({
      accountId: loop.accountId,
      loopId: loop.loopId,
      set: { state: "between_laps", waitReason: reason, updatedAt: this.iso() },
      expect: { currentRunId: null, lapRuns: loop.lapRuns, states: ["between_laps"] },
    });
  }

  // 周回の開始が断られた。同時の数と古い版は待ち、月の token・キー・導入は止める
  private async refused(loop: DevLoop, err: ApiError): Promise<void> {
    const body = err.body().error as { code: string; message: string; details?: { reasons?: { quantity: string }[] } };
    const reasons = body.details?.reasons ?? [];
    if (body.code === "limits_exceeded" && reasons.length > 0 && reasons.every((r) => r.quantity.startsWith("concurrent_"))) {
      await this.setWaiting(loop, "slots");
      return;
    }
    if (body.code === "parallel_unsupported_version") {
      await this.setWaiting(loop, "legacy_version");
      return;
    }
    const now = this.iso();
    await this.deps.table.writeLoop({
      accountId: loop.accountId,
      loopId: loop.loopId,
      set: {
        state: "stopped",
        waitReason: null,
        stopCondition: body.code === "limits_exceeded" ? "limits_exceeded" : "launch_refused",
        stopDetail: JSON.stringify({ code: body.code, message: body.message, ...(body.details ? { details: body.details } : {}) }).slice(0, 4000),
        updatedAt: now,
      },
      expect: { currentRunId: null, lapRuns: loop.lapRuns, states: ["between_laps"] },
      releaseMilestones: { repositoryId: loop.repositoryId, titles: loop.milestones },
    });
  }

  // 停止の後の処理で枠が減ったとき、同じアカウントの待っているループを古い順に起こす
  async wakeWaitingLoops(accountId: string): Promise<void> {
    // 他のアカウントの待ちは取得の時点で除く（ループの行を読みに行かない）
    for (const loop of await this.deps.table.listWaitingLoops(accountId)) {
      await this.startNextLap(loop.accountId, loop.loopId, false);
      const after = await this.deps.table.getLoop(loop.accountId, loop.loopId);
      if (after?.state === "between_laps" && after.waitReason === "slots") return;
    }
  }

  // 5 分ごとの見回り。次の周回を待つループをすべて起こし直す（user_start は操作だけが起こす。I52）
  async sweepLoops(): Promise<void> {
    for (const loop of await this.deps.table.listWaitingLoops()) {
      await this.startNextLap(loop.accountId, loop.loopId, false).catch((err: Error) =>
        this.log({ event: "loop_start_failed", loop_id: loop.loopId, error: err.name }),
      );
    }
  }

  // F2: 止める。動いている実行があればスプリントの実行の取り消しと同じ形で止め、停止の後に cancelled へ移す（決定 15）
  async cancelLoop(ctx: AccountContext, loopId: string): Promise<{ state: DevLoop["state"] }> {
    const { table, control } = this.deps;
    for (let attempt = 0; attempt < START_RETRIES; attempt++) {
      const loop = await this.loopOf(ctx, loopId);
      if (isTerminalLoop(loop.state)) throw new ConflictError("already_finished", "このループは終わっています");
      const now = this.iso();
      if (loop.currentRunId === null) {
        const ok = await table.writeLoop({
          accountId: loop.accountId,
          loopId: loop.loopId,
          set: { state: "cancelled", waitReason: null, cancelRequested: true, updatedAt: now },
          expect: { currentRunId: null, lapRuns: loop.lapRuns, states: ["between_laps"] },
          releaseMilestones: { repositoryId: loop.repositoryId, titles: loop.milestones },
        });
        if (ok) return { state: "cancelled" };
        continue;
      }
      // 周回が切り替わって今の実行が変わっていれば、読み直して新しい実行を止め直す
      const requested = await table.writeLoop({
        accountId: loop.accountId,
        loopId: loop.loopId,
        set: { cancelRequested: true, updatedAt: now },
        expect: { currentRunId: loop.currentRunId },
      });
      if (!requested) continue;
      await control.cancelSprintRun(ctx, loop.currentRunId).catch((err) => {
        if (!(err instanceof ApiError)) throw err;
      });
      const after = await this.loopOf(ctx, loopId);
      return { state: after.state };
    }
    throw new ConflictError("loop_busy", "ループの状態が変わりました。やり直してください");
  }

  // F2: 予算を変える。終わりの状態でないループにだけ効き、次のジョブの指示から NDF の予算へ渡る（決定 14）
  async updateBudget(ctx: AccountContext, loopId: string, body: Record<string, unknown>) {
    const budget = parseBudget(body.budget ?? {});
    const loop = await this.loopOf(ctx, loopId);
    if (isTerminalLoop(loop.state)) throw new ConflictError("already_finished", "このループは終わっています");
    const ok = await this.deps.table.writeLoop({
      accountId: loop.accountId,
      loopId: loop.loopId,
      set: { budget, ...(ctx.userId === loop.createdBy ? { context: contextOf(ctx) } : {}), updatedAt: this.iso() },
      expect: { states: ["running", "between_laps", "awaiting_approval"] },
    });
    if (!ok) throw new ConflictError("already_finished", "このループは終わっています");
    return loopRow((await this.loopOf(ctx, loopId))!);
  }

  // F4: local のループで次の周回を始める（I52）
  async requestNextLap(ctx: AccountContext, loopId: string): Promise<{ state: DevLoop["state"] }> {
    const loop = await this.loopOf(ctx, loopId);
    if (loop.state !== "between_laps" || loop.waitReason !== "user_start") {
      throw new ConflictError("not_awaiting_user_start", "このループは次の周回を始める操作を待っていません");
    }
    await this.deps.table.writeLoop({
      accountId: loop.accountId,
      loopId: loop.loopId,
      set: { ...(ctx.userId === loop.createdBy ? { context: contextOf(ctx) } : {}), updatedAt: this.iso() },
      expect: { currentRunId: null, lapRuns: loop.lapRuns, states: ["between_laps"] },
    });
    await this.startNextLap(ctx.accountId, loopId, true);
    return { state: (await this.loopOf(ctx, loopId)).state };
  }

  // F6: 人の判断待ちに答える。答えは次のジョブの LOOP_ANSWERS に載り、ランナーが NDF の loop answer で書く
  async answer(ctx: AccountContext, loopId: string, body: Record<string, unknown>) {
    const issue = body.issue;
    const decision = body.decision;
    const pr = body.pr;
    if (!Number.isInteger(issue) || (issue as number) < 1) throw new InvalidRequestError("課題の番号の形ではありません");
    if (decision !== "approved" && decision !== "rejected") throw new InvalidRequestError("答えは approved か rejected です");
    if (pr !== undefined && pr !== null && (!Number.isInteger(pr) || (pr as number) < 1)) throw new InvalidRequestError("Pull Request の番号の形ではありません");
    const answer: LoopAnswer = { number: issue as number, decision: decision as LoopAnswer["decision"], pr: (pr as number | undefined) ?? null };
    // answers は配列を丸ごと置き換えるため、読んだときの配列のままのときだけ書き、変わっていれば読み直す。
    // 同時に別の課題へ答えた人の答えを古い配列で消さない
    for (let attempt = 0; attempt < START_RETRIES; attempt++) {
      const loop = await this.loopOf(ctx, loopId);
      if (isTerminalLoop(loop.state)) throw new ConflictError("already_finished", "このループは終わっています");
      if (!loop.pending.some((p) => p.number === issue)) throw new ConflictError("not_pending", "この課題は人の判断待ちではありません");
      const open = new Set(loop.pending.map((p) => p.number));
      const answers = [...loop.answers.filter((a) => a.number !== issue && open.has(a.number)), answer];
      const ok = await this.deps.table.writeLoop({
        accountId: loop.accountId,
        loopId: loop.loopId,
        set: { answers, updatedAt: this.iso() },
        expect: { states: ["running", "between_laps", "awaiting_approval"], answers: loop.answers },
      });
      if (ok) return { pending: pendingRows((await this.loopOf(ctx, loopId))!) };
    }
    throw new ConflictError("loop_busy", "ループの状態が変わりました。やり直してください");
  }

  // F5: 一覧（新しい順 50 件）
  async listLoops(ctx: AccountContext, repositoryId: number) {
    const loops = (await this.deps.table.listLoops(ctx.accountId)).filter((l) => l.repositoryId === repositoryId).slice(0, LIST_LIMIT);
    return { loops: loops.map(loopRow) };
  }

  // F5: 詳細（締めた周回・周回ごとの実行・人の判断待ち）
  async getLoopDetail(ctx: AccountContext, loopId: string) {
    const loop = await this.loopOf(ctx, loopId);
    const runs = await this.deps.table.listLoopRuns(ctx.accountId, loop.loopId);
    const { laps, summaries } = await this.deps.reader.loopRunsDetail(runs.map((r) => r.sprintRunId));
    const runRows = runs.map((r) => {
        const summary = summaries[r.sprintRunId] ?? { costUsd: 0, sections: 0 };
        return {
          sprint_run_id: r.sprintRunId,
          lap: r.lap ?? 0,
          state: r.state,
          failure_reason: r.failureReason,
          finish_reason: r.finishReason ?? null,
          resume_at: r.resumeAt ?? null,
          cost_usd: summary.costUsd,
          sections: summary.sections,
        };
      });
    return {
      loop: loopRow(loop),
      laps: laps
        .sort((a, b) => a.lap - b.lap)
        .map((l) => ({ lap: l.lap, issues: l.issues, result: l.result, version: l.version, cost_usd: l.cost_usd, seconds: l.seconds })),
      runs: runRows.sort((a, b) => b.lap - a.lap),
      pending: pendingRows(loop),
    };
  }
}

function pendingRows(loop: DevLoop) {
  return loop.pending.map((p) => {
    const a = loop.answers.find((x) => x.number === p.number);
    return { number: p.number, reason: p.reason, lap: p.lap, answer: a ? { decision: a.decision, pr: a.pr } : null };
  });
}

// 一覧の 1 行。周回の数・費用・予算の残りは NDF が締めた周回の値で出す（決定 16）
export function loopRow(loop: DevLoop) {
  const left = (limit: number | null, used: number) => (limit === null ? null : Math.max(0, round(limit - used)));
  return {
    loop_id: loop.loopId,
    name: loop.name,
    milestones: loop.milestones,
    state: loop.state,
    wait_reason: loop.state === "between_laps" ? loop.waitReason : null,
    laps_closed: loop.lapsClosed,
    cost_usd: loop.closedCostUsd,
    budget: loop.budget,
    budget_remaining: {
      laps: left(loop.budget.laps, loop.lapsClosed),
      usd: left(loop.budget.usd, loop.closedCostUsd),
      hours: left(loop.budget.hours, loop.closedSeconds / 3600),
    },
    last_run_at: loop.lastRunAt,
    stop_condition: loop.stopCondition,
    stop_detail: loop.stopDetail,
  };
}
