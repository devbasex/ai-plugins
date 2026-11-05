// 機能から起こした契約の形と操作の表（#2384 の受け入れ条件 4・5・16、不変条件 I10）
import { readdir, readFile } from "node:fs/promises";
import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import { openapiDir, readOperations } from "../scripts/operations.ts";
import { AUDIT_KEYS, OPERATIONS, operation, operationUrl } from "../src/operations.ts";

type Obj = Record<string, any>;
const METHODS = ["get", "post", "put", "patch", "delete"];

async function allOperations(): Promise<{ file: string; path: string; method: string; op: Obj; doc: Obj }[]> {
  const out = [];
  for (const file of (await readdir(openapiDir)).filter((f) => f.endsWith(".yaml") && !f.startsWith("."))) {
    const doc = yaml.load(await readFile(`${openapiDir}/${file}`, "utf8")) as Obj;
    for (const [path, item] of Object.entries(doc.paths as Obj)) {
      for (const method of METHODS) if (item[method]) out.push({ file, path, method, op: item[method] as Obj, doc });
    }
  }
  return out;
}

function resolve(doc: Obj, schema: Obj): Obj {
  if (typeof schema.$ref !== "string") return schema;
  return schema.$ref
    .slice(2)
    .split("/")
    .reduce((node: Obj, part: string) => node[part], doc);
}

describe("利用者向けの操作の形（受け入れ条件 4・5）", () => {
  it("成功の応答と要求の本文が配列である", async () => {
    const userOps = (await allOperations()).filter((o) => o.op["x-ndf-access"] !== undefined);
    expect(userOps.length).toBe(OPERATIONS.length);
    for (const { op, doc } of userOps) {
      const ok = op.responses["200"];
      expect(ok, `${op.operationId} の 200`).toBeDefined();
      expect(resolve(doc, ok.content["application/json"].schema).type, op.operationId).toBe("array");
      for (const status of Object.keys(op.responses)) expect(["200", "400", "401", "403", "503"], `${op.operationId} の ${status}`).toContain(status);
      if (op.requestBody) {
        const schema = resolve(doc, op.requestBody.content["application/json"].schema);
        expect(schema.type, op.operationId).toBe("array");
        expect([schema.minItems, schema.maxItems], op.operationId).toEqual([1, 100]);
      }
    }
  });

  it("経路の変数はアカウントの ID だけで、単一のレコードを経路で指す取得が無い", async () => {
    for (const { path, op } of await allOperations()) {
      if (op["x-ndf-access"] === undefined) continue;
      const vars = [...path.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
      expect(vars.filter((v) => v !== "accountId"), `${op.operationId} の ${path}`).toEqual([]);
    }
  });

  it("要素を持つ操作の鍵が、query か本文の要素の項目にある", async () => {
    for (const op of OPERATIONS) {
      if (op.kind === "list") continue;
      for (const key of op.keys) {
        const inQuery = op.parameters.some((p) => p.name === key && p.array);
        const item = op.itemSchema as { properties?: Record<string, unknown> } | null;
        expect(inQuery || Boolean(item?.properties?.[key]), `${op.operationId} の ${key}`).toBe(true);
      }
    }
  });

  it("ランナー向けの経路と受け口は操作の表に入らない", () => {
    for (const op of OPERATIONS) expect(op.path.startsWith("/run-jobs/") || op.path.startsWith("/ingest/"), op.operationId).toBe(false);
  });
});

describe("操作の扱い（受け入れ条件 16、決定 10）", () => {
  it("人だけが確定できる操作は 10 で、ループの判断待ちへの答えを含む", () => {
    const human = OPERATIONS.filter((op) => op.access === "human").map((op) => op.operationId);
    expect(human.sort()).toEqual(
      [
        "addSeat",
        "answerLoopPending",
        "changeSeat",
        "createDelegation",
        "createTeam",
        "deleteModelCredential",
        "putModelCredential",
        "removeSeat",
        "resolveStalledRelease",
        "revokeDelegation",
      ].sort(),
    );
    expect(operation("answerLoopPending").access).toBe("human");
  });

  it("読み取りの操作は GET だけで、GET の操作はすべて読み取り", () => {
    for (const op of OPERATIONS) expect(op.method === "GET", op.operationId).toBe(op.access === "read");
  });
});

describe("操作の記録へ写す鍵（決定 12）", () => {
  it("すべての操作の鍵と作った識別子の項目が、許可した鍵の一覧にある", () => {
    const allowed: readonly string[] = AUDIT_KEYS;
    for (const op of OPERATIONS) {
      for (const key of op.keys) expect(allowed, `${op.operationId} の ${key}`).toContain(key);
      if (op.idField) expect(allowed, `${op.operationId} の ${op.idField}`).toContain(op.idField.split(".").at(-1));
    }
  });
});

describe("操作の表の生成", () => {
  it("契約から読んだ操作と表が一致する", async () => {
    expect((await readOperations()).map((o) => o.operationId)).toEqual(OPERATIONS.map((o) => o.operationId));
  });

  it("経路を表から組み、鍵の query を繰り返す", () => {
    expect(operationUrl(operation("getSprintRunDetail"), "a/1", { sprint_run_id: ["s1", "s2"] })).toBe(
      "/accounts/a%2F1/sprint-runs/detail?sprint_run_id=s1&sprint_run_id=s2",
    );
    expect(operationUrl(operation("getMe"), null)).toBe("/me");
    expect(() => operationUrl(operation("listLoops"), null)).toThrow();
  });
});
