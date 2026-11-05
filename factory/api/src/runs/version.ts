// NDF の版の比べ方（#2347 の決定 12）。版の @ より前を semver として読み、開発版の接尾辞は前置きの版として小さく読む
interface Parsed {
  core: [number, number, number];
  pre: string[];
}

function parse(version: string): Parsed | null {
  const head = version.split("@")[0]!.trim().replace(/^v/, "");
  const m = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$/.exec(head);
  if (!m) return null;
  return { core: [Number(m[1]), Number(m[2]), Number(m[3])], pre: m[4] ? m[4].split(".") : [] };
}

function comparePre(a: string[], b: string[]): number {
  if (a.length === 0 || b.length === 0) return a.length === b.length ? 0 : a.length === 0 ? 1 : -1;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i];
    const y = b[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const nx = /^\d+$/.test(x);
    const ny = /^\d+$/.test(y);
    if (nx && ny && Number(x) !== Number(y)) return Number(x) < Number(y) ? -1 : 1;
    if (nx !== ny) return nx ? -1 : 1;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

// a < b なら負、等しければ 0、a > b なら正。読めない版は null
export function compareVersions(a: string, b: string): number | null {
  const x = parse(a);
  const y = parse(b);
  if (!x || !y) return null;
  for (let i = 0; i < 3; i++) if (x.core[i] !== y.core[i]) return x.core[i]! < y.core[i]! ? -1 : 1;
  return comparePre(x.pre, y.pre);
}

// 並行を許さない古い版か。下限が無い・読めない版は古い版として扱う（安全な側）
export function isLegacyVersion(version: string, minVersion: string | null | undefined): boolean {
  if (!minVersion) return true;
  const c = compareVersions(version, minVersion);
  return c === null || c < 0;
}

// 予算の単位を既定へ戻す指示（default）を NDF が受ける版か（#2406 の決定 4）。下限が無い・読めない版は戻せない側に倒す
export function supportsBudgetReset(version: string, minVersion: string | null | undefined): boolean {
  if (!minVersion) return false;
  const c = compareVersions(version, minVersion);
  return c !== null && c >= 0;
}
