// 外へ送る本文（作業の報告・ログ・判断の文・ファイルのパス）から秘密を伏せる（#2172 の決定 7）。
// 形の規則は NDF の plugins/ndf/scripts/lib/secret_redact.py の 4 つを写したもの（factory/ から plugins/ を参照しないため写す）。
// 規則を変えるときは両方を直す。行と字の数の切り詰め（redact_output の 5 行・500 字）は写さない
export const MASK = "***";

const PATTERNS: [RegExp, string][] = [
  // URL の資格情報（https://user:pass@host・https://x-access-token:<token>@host・https://<token>@host）
  [/(?<=:\/\/)[^\s/@]+@/g, `${MASK}@`],
  // GitHub の token の形
  [/\b(?:gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,})/g, MASK],
  // x-access-token:<値> が URL の外に現れたとき
  [/(?<=x-access-token:)[^\s@]+/g, MASK],
  // 認証ヘッダー（Authorization: Bearer <値>・Proxy-Authorization: ...）は値ごと伏せる
  [/\b((?:proxy-)?authorization\s*[:=]\s*)[^\r\n]+/gi, `$1${MASK}`],
];

// 短すぎる値は置き換えない（ありふれた語まで伏せないため）
const MIN_SECRET_LENGTH = 8;

// secrets はランナーが持つ秘密の値（実行ジョブの token・導入の token・持ち込んだキー・引き受けたロールの資格）。形に頼らず文字列のまま伏せる
export function redact(text: string, secrets: readonly string[] = []): string {
  let out = text;
  for (const value of [...secrets].filter((s) => s.length >= MIN_SECRET_LENGTH).sort((a, b) => b.length - a.length)) {
    out = out.split(value).join(MASK);
  }
  for (const [pattern, repl] of PATTERNS) out = out.replace(pattern, repl);
  return out;
}
