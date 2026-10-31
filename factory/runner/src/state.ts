// 状態の保存と復元（受け入れ条件 28・32、I42）。保存は NDF の状態ディレクトリ（追跡しないパス）だけを tar にし、
// 復元は追跡するパスを 1 つでも含む保存を捨てる。取り込んだリポジトリの .ndf/ を上書きしない
import { execFileSync } from "node:child_process";

export const STATE_PATHS = [".ndf/tmp", ".ndf/loop"];

export type Exec = (cmd: string, args: string[], cwd: string) => string;
export const exec: Exec = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });

export function trackedFiles(repoDir: string, run: Exec = exec): Set<string> {
  return new Set(run("git", ["ls-files", "-z"], repoDir).split("\0").filter(Boolean));
}

// 保存の中のパスのうち、追跡するパスか、状態ディレクトリの外に当たるもの
export function trackedEntries(entries: string[], tracked: Set<string>): string[] {
  const inState = (e: string) => STATE_PATHS.some((s) => e === s || e.startsWith(`${s}/`));
  return entries
    .map((e) => e.replace(/^\.\//, "").replace(/\/$/, ""))
    .filter((e) => e !== "" && (tracked.has(e) || !inState(e)));
}

export class StateRejectedError extends Error {
  constructor(readonly paths: string[]) {
    super(`保存した状態が追跡するパスを含むため戻しません（${paths.length} 件）`);
    this.name = "StateRejectedError";
  }
}

// 保存する。追跡しない状態ディレクトリだけを tar にする
export function packState(repoDir: string, archive: string, run: Exec = exec): void {
  const present = STATE_PATHS.filter((p) => {
    try {
      run("test", ["-e", p], repoDir);
      return true;
    } catch {
      return false;
    }
  });
  run("tar", ["-czf", archive, "--", ...(present.length > 0 ? present : ["--files-from=/dev/null"])], repoDir);
}

// 戻す。追跡するパスを含めば何も書かずに StateRejectedError
export function unpackState(repoDir: string, archive: string, run: Exec = exec): void {
  const entries = run("tar", ["-tzf", archive], repoDir).split("\n").filter(Boolean);
  const bad = trackedEntries(entries, trackedFiles(repoDir, run));
  const outside = entries.filter((e) => e.startsWith("/") || e.split("/").includes(".."));
  if (bad.length > 0 || outside.length > 0) throw new StateRejectedError([...bad, ...outside]);
  run("tar", ["-xzf", archive], repoDir);
}
