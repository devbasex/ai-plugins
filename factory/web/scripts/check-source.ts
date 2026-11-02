// 画面・部品・文脈のコードに表示の文言を直に書いていないか、画面がダミーの実装を import していないかを見る
// 対象は src/**/*.{ts,tsx}。*.test.*・src/dummy/・翻訳のファイル（JSON）は見ない。コメントは見ない
// 終了コード: 0 無し / 1 見つけた / 2 構文解析できない
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { parseSync } from "oxc-parser";

export interface Finding {
  file: string;
  line: number;
  column: number;
  rule: "jsx-text" | "jsx-attr" | "jsx-expr" | "non-ascii" | "dummy-import";
  text: string;
}

const LETTER = /\p{L}/u;
const NON_ASCII = /[^\x00-\x7f]/;
const TEXT_ATTRS = new Set(["aria-label", "title", "placeholder", "alt", "label"]);

interface Node {
  type: string;
  start: number;
  end: number;
  [key: string]: unknown;
}

function isNode(value: unknown): value is Node {
  return typeof value === "object" && value !== null && typeof (value as Node).type === "string";
}

function children(node: Node): Node[] {
  const out: Node[] = [];
  for (const [key, value] of Object.entries(node)) {
    if (key === "parent") continue;
    if (Array.isArray(value)) for (const v of value) if (isNode(v)) out.push(v);
    if (isNode(value)) out.push(value);
  }
  return out;
}

function stringOf(node: Node): string | null {
  if (node.type === "Literal" && typeof node.value === "string") return node.value;
  if (node.type === "TemplateLiteral") {
    return (node.quasis as Array<{ value: { cooked: string | null; raw: string } }>).map((q) => q.value.cooked ?? q.value.raw).join("");
  }
  return null;
}

function lineColumn(code: string, offset: number): { line: number; column: number } {
  const before = code.slice(0, offset);
  const line = before.split("\n").length;
  return { line, column: offset - before.lastIndexOf("\n") };
}

function isDummyPath(spec: string): boolean {
  return /(^|\/)dummy(\/|$)/.test(spec);
}

// 1 つの源を検査する。file は src からの相対の位置（main.tsx だけがダミーを import してよい）
export function checkSource(file: string, code: string): Finding[] {
  const result = parseSync(file, code, { lang: file.endsWith(".tsx") ? "tsx" : "ts" });
  if (result.errors.length > 0) throw new Error(`${file}: ${result.errors[0]!.message}`);
  const findings: Finding[] = [];
  const report = (node: Node, rule: Finding["rule"], text: string) => findings.push({ file, ...lineColumn(code, node.start), rule, text: text.trim() });
  const allowDummy = file === "main.tsx" || file === "src/main.tsx";

  // 1 つの文字列の節は 1 つの規則でだけ報告する（JSX の規則を先に当てる）
  const reported = new Set<Node>();
  const visit = (node: Node) => {
    switch (node.type) {
      case "JSXText": {
        const value = node.value as string;
        if (LETTER.test(value)) report(node, "jsx-text", value);
        break;
      }
      case "JSXAttribute": {
        const name = node.name as Node;
        const value = node.value as Node | null;
        if (name.type === "JSXIdentifier" && TEXT_ATTRS.has(name.name as string) && value) {
          const literal = value.type === "JSXExpressionContainer" ? (value.expression as Node) : value;
          const text = stringOf(literal);
          if (text !== null && LETTER.test(text)) {
            report(literal, "jsx-attr", text);
            reported.add(literal);
          }
        }
        break;
      }
      case "JSXElement":
      case "JSXFragment": {
        // JSX の子に置いた文字列・テンプレート（属性の値は jsx-attr が見る）
        for (const child of node.children as Node[]) {
          if (child.type !== "JSXExpressionContainer") continue;
          const expression = child.expression as Node;
          const text = stringOf(expression);
          if (text !== null && LETTER.test(text)) {
            report(expression, "jsx-expr", text);
            reported.add(expression);
          }
        }
        break;
      }
      case "ImportDeclaration":
      case "ImportExpression":
      case "ExportAllDeclaration":
      case "ExportNamedDeclaration": {
        const source = node.source as Node | null | undefined;
        const spec = source ? stringOf(source) : null;
        if (spec !== null && !allowDummy && isDummyPath(spec)) {
          report(source!, "dummy-import", spec);
          reported.add(source!);
        }
        break;
      }
    }
    for (const child of children(node)) visit(child);
    if ((node.type === "Literal" || node.type === "TemplateLiteral") && !reported.has(node)) {
      const text = stringOf(node);
      if (text !== null && NON_ASCII.test(text)) report(node, "non-ascii", text);
    }
  };
  visit(result.program as unknown as Node);
  return findings.sort((a, b) => a.line - b.line || a.column - b.column);
}

function excluded(rel: string): boolean {
  const parts = rel.split("/");
  return parts[0] === "dummy" || /\.test\.tsx?$/.test(rel) || !/\.tsx?$/.test(rel) || rel.endsWith(".d.ts");
}

function listSources(dir: string, root: string = dir): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return listSources(full, root);
    const rel = relative(root, full).split(sep).join("/");
    return excluded(rel) ? [] : [rel];
  });
}

export function checkTree(srcDir: string): Finding[] {
  return listSources(srcDir).flatMap((rel) => checkSource(rel, readFileSync(join(srcDir, rel), "utf8")));
}

function main(): number {
  const srcDir = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");
  let findings: Finding[];
  try {
    findings = checkTree(srcDir);
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    return 2;
  }
  for (const f of findings) console.log(`src/${f.file}:${f.line}:${f.column} ${f.rule} ${JSON.stringify(f.text)}`);
  return findings.length > 0 ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(main());
