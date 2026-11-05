// 操作の記録（#2384 の受け入れ条件 19〜23、不変条件 I7〜I9・I16）
import { beforeEach, describe, expect, it } from "vitest";
import { createTestApi, firstResult, type TestApi } from "../testing/app.ts";
import { DynamoAuditLog } from "./dynamo-log.ts";
import { AUDIT_COLUMNS } from "./log.ts";
import { MemoryAuditLog } from "./memory-log.ts";
import { StructuredLogAudit } from "./structured-log.ts";

let api: TestApi;
beforeEach(async () => {
  api = await createTestApi({ factory: true });
});

async function personal(user: string): Promise<string> {
  return (await (await api.op("getMe", { user })).json())[0].personal_account_id;
}

const last = () => api.audit.items.at(-1)!;

describe("1 回の操作に 1 件（受け入れ条件 19・20、I7）", () => {
  it("成功の記録は 7 項目と結果を持ち、要求の識別子でログの行とたどれる", async () => {
    const acc = await personal("u-1");
    const before = api.audit.items.length;
    await api.op("listSprintRuns", { user: "u-1", accountId: acc });
    expect(api.audit.items.length).toBe(before + 1);
    expect(last()).toEqual({
      record_id: expect.any(String),
      at: expect.stringMatching(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/),
      user_id: "u-1",
      credential_id: null,
      via: "screen",
      operation_id: "listSprintRuns",
      account_id: acc,
      target_ids: [],
      outcome: "ok",
      status: 200,
      reason: null,
    });
    expect(api.logs.at(-1)!.request_id).toBe(last().record_id);
  });

  it("一部の要素が失敗すると partial と、失敗の code を重ねずに持つ。対象の ID は要素の鍵", async () => {
    const acc = await personal("u-1");
    await api.call("GET", `/accounts/${acc}/sprint-runs/detail?sprint_run_id=sr-a&sprint_run_id=sr-b`, { user: "u-1" });
    expect(last()).toMatchObject({ outcome: "partial", reason: "forbidden", status: 200, target_ids: ["sprint_run_id=sr-a", "sprint_run_id=sr-b"] });
  });

  it("読み取りも、401・403・400 で断った要求も 1 件ずつ残る", async () => {
    const acc = await personal("u-1");
    await personal("u-2");
    const before = api.audit.items.length;
    await api.op("getMe", { authorization: "Bearer broken" });
    expect(last()).toMatchObject({ outcome: "rejected", status: 401, reason: "unauthorized", user_id: null, operation_id: "getMe", via: "screen" });
    await api.op("listSprintRuns", { user: "u-2", accountId: acc });
    expect(last()).toMatchObject({ outcome: "rejected", status: 403, reason: "forbidden", user_id: "u-2", operation_id: "listSprintRuns", account_id: acc });
    await api.call("GET", `/accounts/${acc}/sprint-runs/detail`, { user: "u-1" });
    expect(last()).toMatchObject({ outcome: "rejected", status: 400, reason: "empty_items" });
    await api.call("GET", "/nowhere", { user: "u-1" });
    expect(last()).toMatchObject({ status: 404, operation_id: null });
    expect(api.audit.items.length).toBe(before + 4);
  });

  it("委任の資格情報での要求は資格情報の ID と api を持ち、断った人だけの操作も残る", async () => {
    const acc = await personal("u-1");
    const issued = await firstResult(
      await api.op("createDelegation", { user: "u-1", items: [{ client_name: "見本のクライアント", scope: "read", expires_in_seconds: 600, idempotency_key: "a" }] }),
    );
    expect(last()).toMatchObject({ operation_id: "createDelegation", target_ids: ["idempotency_key=a", `credential_id=${issued.body.delegation.credential_id}`] });
    const auth = `Bearer ${issued.body.credential}`;
    await api.op("listSprintRuns", { authorization: auth, accountId: acc });
    expect(last()).toMatchObject({ via: "api", credential_id: issued.body.delegation.credential_id, user_id: "u-1", outcome: "ok" });
    await api.op("putModelCredential", { authorization: auth, accountId: acc, items: [{ kind: "anthropic_api_key", api_key: "k" }] });
    expect(last()).toMatchObject({ outcome: "rejected", status: 403, reason: "human_only" });
  });

  it("外から付けた呼び出し元のヘッダーでは種類が変わらない（I16）", async () => {
    await personal("u-1");
    const res = await api.app.request("/me", { headers: { Authorization: await api.tokens.bearer("u-1"), "x-ndf-via": "mcp" } });
    expect(res.status).toBe(200);
    expect(last().via).toBe("screen");
  });

  it("追記に失敗したら 503 audit_unavailable を返す", async () => {
    await personal("u-1");
    api.audit.fail = true;
    const res = await api.op("getMe", { user: "u-1" });
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("audit_unavailable");
  });
});

describe("書き換えと削除の経路が無い（受け入れ条件 21、I8）", () => {
  it("保存の実装は追記の関数だけを持つ", () => {
    for (const cls of [MemoryAuditLog, DynamoAuditLog, StructuredLogAudit]) {
      expect(Object.getOwnPropertyNames(cls.prototype).sort(), cls.name).toEqual(["append", "constructor"]);
    }
  });
});

describe("秘密と個人情報を書かない（受け入れ条件 22・23、I9）", () => {
  it("キー・ロールの ARN・ログインの token・委任の値・GitHub の code と proof・名前を含む要求でも、記録に値が入らない", async () => {
    const acc = await personal("u-1");
    const login = await api.tokens.bearer("u-1");
    const secrets = [
      "sk-ant-SECRET-0001",
      "arn:aws:iam::123456789012:role/secret-role",
      login.slice("Bearer ".length),
      "gh-code-SECRET",
      "gh-state-SECRET",
      "proof-SECRET",
      "秘密のチーム",
      "秘密のクライアント",
    ];
    await api.op("putModelCredential", { user: "u-1", accountId: acc, items: [{ kind: "anthropic_api_key", api_key: secrets[0] }] });
    await api.op("putModelCredential", { user: "u-1", accountId: acc, items: [{ kind: "aws_role", role_arn: secrets[1], region: "us-east-1" }] });
    await api.op("postRepositoryCandidates", { user: "u-1", accountId: acc, items: [{ code: secrets[3], state: secrets[4] }] });
    await api.op("registerRepository", { user: "u-1", accountId: acc, items: [{ proof: secrets[5], repository_id: 101 }] });
    await api.op("createTeam", { user: "u-1", items: [{ name: secrets[6], seat_type: "standard", idempotency_key: "t" }] });
    const issued = await firstResult(
      await api.op("createDelegation", { user: "u-1", items: [{ client_name: secrets[7], scope: "read", expires_in_seconds: 600, idempotency_key: "c" }] }),
    );
    await api.op("getMe", { authorization: `Bearer ${issued.body.credential}` });
    const text = JSON.stringify(api.audit.items);
    for (const s of [...secrets, issued.body.credential]) expect(text, s).not.toContain(s);
  });

  it("記録の列は固定の一覧だけで、メールアドレス・氏名・課題の題と本文を写す列を持たない", async () => {
    await personal("u-1");
    for (const item of api.audit.items) expect(Object.keys(item).sort()).toEqual([...AUDIT_COLUMNS].sort());
  });
});
