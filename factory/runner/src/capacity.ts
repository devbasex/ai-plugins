// 同時のジョブの数の目安（決定 13）。2026-09-25 の実測（8 コア・30 GB で全体テストの同時実行 6 本が上限）から、
// 1 本あたり 4/3 コア・5 GiB とする。手元のマシン（#2189）が登録と心拍の前に呼び、申告が目安を超えれば警告する（申告は変えない）
import { cpus, totalmem } from "node:os";

const GIB = 1024 ** 3;

export function capacityHint(cpuCount: number, memoryBytes: number): number {
  return Math.max(1, Math.min(Math.floor((cpuCount * 3) / 4), Math.floor(memoryBytes / GIB / 5)));
}

export function localCapacityHint(): number {
  return capacityHint(cpus().length, totalmem());
}

// 申告（無ければ 1）と目安を比べ、超えていれば標準エラーへ 1 行を出す。申告はそのまま返す
export function checkMaxJobs(maxJobs: number | undefined, hint: number, stderr: (line: string) => void): number {
  const declared = maxJobs ?? 1;
  if (declared > hint) {
    stderr(`同時のジョブの数 ${declared} はこの機械の目安 ${hint} を超えています（CPU の数とメモリから出した値）。申告はそのまま送ります`);
  }
  return declared;
}
