// 子課題が共有する契約の入口。型は schemas/ の定義から生成したもの（決定 1・2）、検査は同じ定義から作る（決定 3）
import { Ajv2020, type ValidateFunction } from "ajv/dist/2020.js";
import * as formats from "ajv-formats";
import progressEventSchema from "../schemas/progress-event.schema.json" with { type: "json" };
import runJobInstructionSchema from "../schemas/run-job-instruction.schema.json" with { type: "json" };
import ingestResponseSchema from "../schemas/ingest-response.schema.json" with { type: "json" };
import localRunnerDeclarationSchema from "../schemas/local-runner-declaration.schema.json" with { type: "json" };
import type { LocalRunnerDeclaration } from "./generated/local-runner-declaration.ts";
import type { ProgressEvent } from "./generated/progress-event.ts";
import type { RunJobInstruction } from "./generated/run-job-instruction.ts";

export type * from "./generated/progress-event.ts";
export type * from "./generated/run-job-instruction.ts";
export type * from "./generated/ingest-response.ts";
export type * from "./generated/local-runner-declaration.ts";
export { progressEventSchema, runJobInstructionSchema, ingestResponseSchema, localRunnerDeclarationSchema };

// 進捗の事象の 21 種類と、定義の中の名前（$defs）
export const EVENT_DEFINITIONS = {
  "job.started": "JobStartedEvent",
  "phase.changed": "PhaseChangedEvent",
  "step.recorded": "StepRecordedEvent",
  attention: "AttentionEvent",
  "gate.reached": "GateReachedEvent",
  "gate.answered": "GateAnsweredEvent",
  "usage.recorded": "UsageRecordedEvent",
  "job.finished": "JobFinishedEvent",
  "pr.updated": "PrUpdatedEvent",
  "ci.updated": "CiUpdatedEvent",
  "decision.delegated": "DecisionDelegatedEvent",
  "issue.requested": "IssueRequestedEvent",
  "issue.request_resolved": "IssueRequestResolvedEvent",
  "section.started": "SectionStartedEvent",
  "loop.lap_closed": "LoopLapClosedEvent",
  "loop.stopped": "LoopStoppedEvent",
  "release.requested": "ReleaseRequestedEvent",
  "release.presented": "ReleasePresentedEvent",
  "release.progress": "ReleaseProgressEvent",
  "release.settled": "ReleaseSettledEvent",
  "release.answered": "ReleaseAnsweredEvent",
} as const satisfies Record<ProgressEvent["type"], string>;

export type EventType = keyof typeof EVENT_DEFINITIONS;
export const EVENT_TYPES = Object.keys(EVENT_DEFINITIONS) as EventType[];

export const isEventType = (value: unknown): value is EventType =>
  typeof value === "string" && Object.hasOwn(EVENT_DEFINITIONS, value);

// 検査の失敗の位置と規則の名前。値は含めない（決定 11）
export interface ContractViolation {
  instancePath: string;
  keyword: string;
}

export type ContractResult<T> = { ok: true; value: T } | { ok: false; violation: ContractViolation };

// ajv-formats は CommonJS の既定の export のため、NodeNext の読み込みで関数を取り出す
const addFormats = (formats as unknown as { default: (ajv: Ajv2020) => Ajv2020 }).default;

const ajv = new Ajv2020({ allErrors: false, strict: true, strictRequired: false });
addFormats(ajv);
ajv.addSchema(progressEventSchema);
ajv.addSchema(runJobInstructionSchema);
ajv.addSchema(ingestResponseSchema);
ajv.addSchema(localRunnerDeclarationSchema);

const eventValidators = new Map<EventType, ValidateFunction>(
  EVENT_TYPES.map((t) => [t, ajv.getSchema(`${progressEventSchema.$id}#/$defs/${EVENT_DEFINITIONS[t]}`)!]),
);
const instructionValidator = ajv.getSchema(runJobInstructionSchema.$id)!;
const responseValidator = ajv.getSchema(ingestResponseSchema.$id)!;
const declarationValidator = ajv.getSchema(localRunnerDeclarationSchema.$id)!;

function firstViolation(v: ValidateFunction): ContractViolation {
  const e = v.errors?.[0];
  return { instancePath: e?.instancePath ?? "", keyword: e?.keyword ?? "unknown" };
}

// 進捗の事象 1 件を定義で確かめる。種類が定義のどれでもなければ /type の enum で落とす
export function validateProgressEvent(value: unknown): ContractResult<ProgressEvent> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { ok: false, violation: { instancePath: "", keyword: "type" } };
  }
  const type = (value as { type?: unknown }).type;
  if (!isEventType(type)) return { ok: false, violation: { instancePath: "/type", keyword: "enum" } };
  const validate = eventValidators.get(type)!;
  return validate(value) ? { ok: true, value: value as ProgressEvent } : { ok: false, violation: firstViolation(validate) };
}

// 実行ジョブの指示を定義で確かめる。START・EXECUTION_MODE による必須の違いは型に出ないため、ここで縛る
export function validateRunJobInstruction(value: unknown): ContractResult<RunJobInstruction> {
  return instructionValidator(value)
    ? { ok: true, value: value as RunJobInstruction }
    : { ok: false, violation: firstViolation(instructionValidator) };
}

// 受け口の応答の本文が定義に合うか
export function isIngestResponse(value: unknown): boolean {
  return responseValidator(value) as boolean;
}

// 手元のマシンの申告（登録と心拍の本文の一部）を定義で確かめる
export function validateLocalRunnerDeclaration(value: unknown): ContractResult<LocalRunnerDeclaration> {
  return declarationValidator(value)
    ? { ok: true, value: value as LocalRunnerDeclaration }
    : { ok: false, violation: firstViolation(declarationValidator) };
}

export { PHASE_NAMES, CLOSING_STEPS, isPhaseName, type PhaseName } from "./phases.ts";

// 実行のレーンの名前。ループはループ 1 つ、手動の実行は実行 1 つがレーンになる。#2436 のレーン名の形（小文字・32 字まで）に
// 収めるため、ループの名前と実行の識別子を切り詰める。ランナーの laneName と API の laneOf が同じ名前を出すための唯一の式
export const organizerLaneName = (sprintRunId: string, loopName: string | null | undefined): string =>
  loopName ? `loop-${loopName.toLowerCase().slice(0, 27)}` : `run-${sprintRunId.toLowerCase().slice(0, 16)}`;

// リリースの実行の事象の冪等の鍵（#2439）。実行の識別子を頭にする（冪等の鍵の表はサービス全体で 1 つのため、リポジトリごとの
// 連番だけを鍵にしない）。ランナーの送信と API の受け口の照合が同じ式を使うための唯一の定義
export const releasePresentedKey = (sprintRunId: string, k: number) => `${sprintRunId}:presented:${k}`;
export const releaseProgressKey = (sprintRunId: string, stage: string) => `${sprintRunId}:progress:${stage}`;
// 結果は種類ごとに鍵を分ける。同じ実行が failed を送った後に再開して released を送れるように（鍵が 1 つだと重複として捨てられる）
export const releaseSettledKey = (sprintRunId: string, result: string) => `${sprintRunId}:settled:${result}`;
