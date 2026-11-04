import { operation, operationUrl, type OperationId } from "@ndf-factory/contracts/operations";
import { AccountService } from "../accounts/service.ts";
import { MemoryAccountStore } from "../accounts/memory-store.ts";
import { MemoryDelegationStore } from "../delegations/memory-store.ts";
import { DelegationService } from "../delegations/service.ts";
import { createApiApp, type LogEntry } from "../handlers/api.ts";
import { createTokenKit, type TokenKit } from "./tokens.ts";
import type { FactoryDeps } from "../routes/factory.ts";
import { CredentialService, type SecretsPort } from "../credentials/service.ts";
import { RepositoryService } from "../repositories/service.ts";
import { memoryProgressReader } from "../progress/read.ts";
import { LoopControl } from "../runs/loops.ts";
import { createRunsHarness, type RunsHarness } from "./runs.ts";

// 秘密の置き場の偽物（値はメモリだけに置く）
export class FakeSecrets implements SecretsPort {
  readonly values = new Map<string, string>();
  private n = 0;
  async create(name: string, value: string) {
    const arn = `arn:aws:secretsmanager:us-east-1:111111111111:secret:${name}-${++this.n}`;
    this.values.set(arn, value);
    return arn;
  }
  async put(arn: string, value: string) {
    this.values.set(arn, value);
  }
  async delete(arn: string) {
    this.values.delete(arn);
  }
}

// テストで本文を読みやすくするため、json() の型を any にした応答
export type TestResponse = Omit<Response, "json"> & { json(): Promise<any> };

// 操作の呼び出し（#2384）。経路と query は操作の表から組む。items は本文の要素の配列
export interface OpOptions {
  user?: string;
  idp?: "Google" | "GitHub";
  authorization?: string;
  accountId?: string;
  query?: Record<string, unknown>;
  items?: unknown;
}

export function opRequest(operationId: OperationId, opts: OpOptions): { method: string; path: string; body: unknown } {
  const op = operation(operationId);
  return { method: op.method, path: operationUrl(op, opts.accountId ?? null, opts.query), body: opts.items };
}

// 要素 1 個の要求の結果を、今までの 1 件の応答と同じ形（状態と本文）で読む。要求全体を断ったときはその状態と本文
export async function firstResult(res: TestResponse): Promise<{ status: number; body: any }> {
  const data = await res.json();
  if (res.status !== 200 || !Array.isArray(data)) return { status: res.status, body: data };
  const r = data[0];
  if (r && "ok" in r) return r.ok ? { status: 200, body: r.value } : { status: r.status, body: { error: r.error } };
  return { status: 200, body: r };
}

export interface TestApi {
  app: ReturnType<typeof createApiApp>;
  store: MemoryAccountStore;
  service: AccountService;
  delegationStore: MemoryDelegationStore;
  delegations: DelegationService;
  // 時刻を進められる時計（委任の期限の確かめ）
  clock: { now: Date };
  // factory を求めたときだけ。実行の表・GitHub・秘密の置き場の偽物
  h: RunsHarness | null;
  secrets: FakeSecrets;
  tokens: TokenKit;
  logs: LogEntry[];
  call(method: string, path: string, opts?: { user?: string; idp?: "Google" | "GitHub"; body?: unknown; authorization?: string }): Promise<TestResponse>;
  op(operationId: OperationId, opts?: OpOptions): Promise<TestResponse>;
}

export async function createTestApi(opts: { factory?: boolean } = {}): Promise<TestApi> {
  const store = new MemoryAccountStore();
  let n = 0;
  const service = new AccountService({ store, newId: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}` });
  const tokens = await createTokenKit();
  const logs: LogEntry[] = [];
  const clock = { now: new Date("2026-11-04T00:00:00.000Z") };
  const delegationStore = new MemoryDelegationStore();
  const delegations = new DelegationService({ store: delegationStore, now: () => clock.now });
  const secrets = new FakeSecrets();
  const h = opts.factory ? await createRunsHarness() : null;
  let factory: FactoryDeps | null = null;
  if (h) {
    const key = new TextEncoder().encode("proof-key-for-tests-0123456789ab");
    factory = {
      repositories: new RepositoryService({ table: h.table, github: h.github, proofKey: async () => key }),
      credentials: new CredentialService(h.table, secrets),
      control: h.control,
      loops: new LoopControl({ table: h.table, control: h.control, reader: memoryProgressReader(h.progress), now: () => h.clock.now }),
      organizer: h.organizer,
      table: h.table,
      github: h.github,
      candidateModel: async (_s, issues) => issues.map((i) => ({ number: i.number, selected: true, reason: "理由" })),
      reader: memoryProgressReader(h.progress),
    };
  }
  const app = createApiApp(
    () => ({ verifier: tokens.verifier, accounts: service, delegations }),
    (e) => logs.push(e),
    () => null,
    factory && (() => factory!),
  );
  const api: TestApi = {
    app,
    store,
    service,
    delegationStore,
    delegations,
    clock,
    h,
    secrets,
    tokens,
    logs,
    async call(method, path, opts = {}) {
      const headers: Record<string, string> = {};
      if (opts.authorization !== undefined) headers.Authorization = opts.authorization;
      else if (opts.user) headers.Authorization = await tokens.bearer(opts.user, opts.idp);
      if (opts.body !== undefined) headers["Content-Type"] = "application/json";
      return (await app.request(path, {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      })) as TestResponse;
    },
    op(operationId, opts = {}) {
      const { method, path, body } = opRequest(operationId, opts);
      return api.call(method, path, { ...opts, body });
    },
  };
  return api;
}
