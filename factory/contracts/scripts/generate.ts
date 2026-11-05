// JSON Schema（schemas/）から TypeScript の型（src/generated/）を、契約（openapi/）から操作の表を作る。
// 定義が正で、型と表は生成物（決定 2・#2384 の決定 5）
// node scripts/generate.ts           生成した型と表を書く
// node scripts/generate.ts --check   書かずに、コミットした型か表と食い違えば 1 で終わる
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { compile, type JSONSchema } from "json-schema-to-typescript";
import { generateOperations, operationsPath } from "./operations.ts";

const root = fileURLToPath(new URL("..", import.meta.url));

export const SCHEMAS = ["progress-event", "run-job-instruction", "ingest-response", "local-runner-declaration"] as const;

const BANNER = "// 生成したファイル。手で直さない。定義（schemas/）を直して `npm run generate -w contracts` で作り直す";

export const schemaPath = (name: string) => `${root}schemas/${name}.schema.json`;
export const generatedPath = (name: string) => `${root}src/generated/${name}.ts`;

// 定義 1 つから型の文字列を作る
export async function generateOne(name: string): Promise<string> {
  return generateFromSchema(JSON.parse(await readFile(schemaPath(name), "utf8")) as JSONSchema, name);
}

export function generateFromSchema(schema: JSONSchema, name: string): Promise<string> {
  return compile(schema, schema.title ?? name, {
    bannerComment: BANNER,
    additionalProperties: false,
    // 上限つきの配列を組の和に展開しない（上限は定義の検査が縛る）
    maxItems: 1,
    cwd: `${root}schemas`,
    style: { singleQuote: false, printWidth: 120 },
  });
}

// 作り直した型とコミットした型が食い違う定義の名前
export async function staleSchemas(): Promise<string[]> {
  const stale: string[] = [];
  for (const name of SCHEMAS) {
    const committed = await readFile(generatedPath(name), "utf8").catch(() => null);
    if (committed !== (await generateOne(name))) stale.push(name);
  }
  const committed = await readFile(operationsPath, "utf8").catch(() => null);
  if (committed !== (await generateOperations())) stale.push("operations");
  return stale;
}

async function main(): Promise<void> {
  if (process.argv.includes("--check")) {
    const stale = await staleSchemas();
    if (stale.length > 0) {
      console.error(`型が定義と食い違います: ${stale.join(", ")}。npm run generate -w contracts で作り直してください`);
      process.exitCode = 1;
    }
    return;
  }
  for (const name of SCHEMAS) await writeFile(generatedPath(name), await generateOne(name));
  await writeFile(operationsPath, await generateOperations());
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await main();
