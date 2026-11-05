// 契約（openapi/*.yaml）から操作の表（src/generated/operations.ts）を作る（#2384 の決定 5）。
// 利用者向けの操作（x-ndf-access を持つ操作）だけを取り、画面・API・MCP・検査が同じ表を読む
import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";

const root = fileURLToPath(new URL("..", import.meta.url));
export const openapiDir = `${root}openapi`;
export const operationsPath = `${root}src/generated/operations.ts`;

const METHODS = ["get", "post", "put", "patch", "delete"] as const;
const ACCESS = new Set(["read", "write", "human"]);

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
type Obj = { [k: string]: Json };

export interface GeneratedParameter {
  name: string;
  required: boolean;
  array: boolean;
  description: string | null;
  schema: Json;
}

export interface GeneratedOperation {
  operationId: string;
  method: string;
  path: string;
  kind: string;
  access: string;
  keys: string[];
  idField: string | null;
  summary: string;
  description: string;
  parameters: GeneratedParameter[];
  itemSchema: Json | null;
  source: string;
}

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

// 同じファイルの中の $ref（#/components/...）をほどく。ファイルの外の参照は利用者向けの操作に無い
function deref(doc: Obj, value: Json, seen: string[] = []): Json {
  if (Array.isArray(value)) return value.map((v) => deref(doc, v, seen));
  if (!isObj(value)) return value;
  const ref = value.$ref;
  if (typeof ref === "string") {
    if (!ref.startsWith("#/")) throw new Error(`ファイルの外の参照は操作の表に入れられません: ${ref}`);
    if (seen.includes(ref)) throw new Error(`参照が巡っています: ${ref}`);
    let node: Json = doc;
    for (const part of ref.slice(2).split("/")) node = isObj(node) ? (node[part] ?? null) : null;
    if (node === null) throw new Error(`参照が見つかりません: ${ref}`);
    return deref(doc, node, [...seen, ref]);
  }
  return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, deref(doc, v, seen)]));
}

// 種類は経路の形とメソッドから決める（契約の表の「操作の種類と形」）。POST は、作った識別子を返すか、
// 同じ経路に別のメソッドがあれば作成、それ以外（経路の末尾が動詞）は操作
function kindOf(method: string, path: string, op: Obj, pathItem: Obj): string {
  if (method === "get") return Array.isArray(op["x-ndf-keys"]) ? "select" : "list";
  if (method === "delete") return "delete";
  if (method === "put" || method === "patch") return "update";
  const others = METHODS.filter((m) => m !== "post" && isObj(pathItem[m]));
  return typeof op["x-ndf-id"] === "string" || others.length > 0 ? "create" : "action";
}

export async function readOperations(dir = openapiDir): Promise<GeneratedOperation[]> {
  const files = (await readdir(dir)).filter((f) => f.endsWith(".yaml") && !f.startsWith(".")).sort();
  const out: GeneratedOperation[] = [];
  for (const file of files) {
    const doc = yaml.load(await readFile(`${dir}/${file}`, "utf8")) as Obj;
    const paths = isObj(doc.paths) ? doc.paths : {};
    for (const [path, item] of Object.entries(paths)) {
      if (!isObj(item)) continue;
      for (const method of METHODS) {
        const op = item[method];
        if (!isObj(op) || op["x-ndf-access"] === undefined) continue;
        const access = String(op["x-ndf-access"]);
        if (!ACCESS.has(access)) throw new Error(`${op.operationId}: x-ndf-access は read・write・human のどれかです`);
        const params = (Array.isArray(op.parameters) ? op.parameters : []).map((p) => deref(doc, p)).filter(isObj);
        const body = isObj(op.requestBody) ? deref(doc, op.requestBody) : null;
        const bodySchema = isObj(body) && isObj(body.content) && isObj(body.content["application/json"]) ? body.content["application/json"].schema : null;
        out.push({
          operationId: String(op.operationId),
          method: method.toUpperCase(),
          path,
          kind: kindOf(method, path, op, item),
          access,
          keys: Array.isArray(op["x-ndf-keys"]) ? op["x-ndf-keys"].map(String) : [],
          idField: typeof op["x-ndf-id"] === "string" ? op["x-ndf-id"] : null,
          summary: String(op.summary ?? ""),
          description: String(op.description ?? ""),
          parameters: params
            .filter((p) => p.in === "query")
            .map((p) => ({
              name: String(p.name),
              required: p.required === true,
              array: isObj(p.schema) && p.schema.type === "array",
              description: typeof p.description === "string" ? p.description : null,
              schema: p.schema ?? null,
            })),
          itemSchema: isObj(bodySchema) ? (bodySchema.items ?? null) : null,
          source: file,
        });
      }
    }
  }
  return out.sort((a, b) => a.operationId.localeCompare(b.operationId));
}

const BANNER = "// 生成したファイル。手で直さない。契約（openapi/）を直して `npm run generate -w contracts` で作り直す";

export async function generateOperations(dir = openapiDir): Promise<string> {
  const ops = await readOperations(dir);
  return [
    BANNER,
    'import type { Operation } from "../operations.ts";',
    "",
    `export const OPERATIONS = ${JSON.stringify(ops, null, 2)} as const satisfies readonly Operation[];`,
    "",
  ].join("\n");
}
