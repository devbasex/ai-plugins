// デザインのトークン（Design Tokens Format Module の JSON）を CSS の変数の宣言へ写す純粋な関数
// 名前は入れ子の名前を "-" でつなぐ（color.primary → --color-primary）。値の参照（{color.base}）は解く

type TokenValue = string | number | readonly (string | number)[] | { value: number; unit: string };

interface Token {
  $value: TokenValue;
  $type?: string;
  $description?: string;
}

export type TokenTree = { [name: string]: Token | TokenTree | string | undefined };

function isToken(node: unknown): node is Token {
  return typeof node === "object" && node !== null && "$value" in node;
}

function collect(tree: TokenTree, prefix: string[], out: Map<string, TokenValue>): void {
  for (const [name, node] of Object.entries(tree)) {
    if (name.startsWith("$") || typeof node !== "object" || node === null) continue;
    const path = [...prefix, name];
    if (isToken(node)) out.set(path.join("."), node.$value);
    else collect(node, path, out);
  }
}

function render(value: TokenValue, all: Map<string, TokenValue>, seen: Set<string>): string {
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map((v) => (typeof v === "string" && /\s/.test(v) ? JSON.stringify(v) : String(v))).join(", ");
  if (typeof value === "object") {
    const dim = value as { value: number; unit: string };
    return `${dim.value}${dim.unit}`;
  }
  const ref = /^\{([^}]+)\}$/.exec(value as string);
  if (!ref) return value as string;
  const target = ref[1]!;
  const next = all.get(target);
  if (next === undefined || seen.has(target)) throw new Error(`unresolved token reference: ${target}`);
  return render(next, all, new Set([...seen, target]));
}

// トークンの名前と CSS の値の組を返す
export function flattenTokens(tree: TokenTree): Array<[string, string]> {
  const all = new Map<string, TokenValue>();
  collect(tree, [], all);
  return [...all].map(([path, value]) => [`--${path.replaceAll(".", "-")}`, render(value, all, new Set([path]))]);
}

export function tokensToCss(tree: TokenTree): string {
  return `:root {\n${flattenTokens(tree)
    .map(([name, value]) => `  ${name}: ${value};`)
    .join("\n")}\n}\n`;
}
