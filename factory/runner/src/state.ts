// 状態の保存と復元（受け入れ条件 28・32、I42）。保存は NDF の状態ディレクトリと配布の承認資料（追跡しないパス）だけを tar にし、
// 復元は追跡するパスを 1 つでも含む保存を捨てる。取り込んだリポジトリの .ndf/ を上書きしない
import { execFileSync } from "node:child_process";

// .ndf/organizer は Factory のオーガナイザーとの置き場（#2439）。依頼と答えを周回と再開をまたいで残す
export const STATE_PATHS = [".ndf/tmp", ".ndf/loop", ".ndf/organizer"];

// 名前で決まる状態のファイル。配布の提示のプランが作る承認資料（parallel_release.py の plan_parallel_release が
// issues/approval-<プラグイン>-v<版>-parallel-<連番>-<提示>.md に書く）は、承認の後の新しいコンテナで confirm のプランが
// --from approve で読むため、追跡していなければ保存と復元の対象にする
export const STATE_FILE_PATTERNS = [/^issues\/approval-[^/]+-parallel-\d+-\d+\.md$/];

// 保存するパス。開発ループの実行は、そのループの引継ぎ文書（.ndf/handoff/loop-<名>.md）を 1 つ足す（#2347）
export function statePaths(loopName?: string | null): string[] {
  return loopName ? [...STATE_PATHS, `.ndf/handoff/loop-${loopName}.md`] : STATE_PATHS;
}

export type Exec = (cmd: string, args: string[], cwd: string) => string;
export const exec: Exec = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });

export function trackedFiles(repoDir: string, run: Exec = exec): Set<string> {
  return new Set(run("git", ["ls-files", "-z"], repoDir).split("\0").filter(Boolean));
}

// 保存の中のパスのうち、追跡するパスか、状態ディレクトリの外に当たるもの
export function trackedEntries(entries: string[], tracked: Set<string>, paths: string[] = STATE_PATHS): string[] {
  const inState = (e: string) => paths.some((s) => e === s || e.startsWith(`${s}/`)) || STATE_FILE_PATTERNS.some((p) => p.test(e));
  return entries
    .map((e) => e.replace(/^\.\//, "").replace(/\/$/, ""))
    .filter((e) => e !== "" && (tracked.has(e) || !inState(e)));
}

export class StateRejectedError extends Error {
  // Node が型を消すだけで動かすため、parameter property（変換が要る構文）を使わない
  readonly paths: string[];
  constructor(paths: string[]) {
    super(`保存した状態が追跡するパスを含むため戻しません（${paths.length} 件）`);
    this.name = "StateRejectedError";
    this.paths = paths;
  }
}

// 名前で決まる状態のファイルのうち、追跡しないもの（無視するファイルも含む）
export function untrackedStateFiles(repoDir: string, run: Exec = exec): string[] {
  try {
    return run("git", ["ls-files", "--others", "-z", "--", "issues"], repoDir)
      .split("\0")
      .filter((f) => STATE_FILE_PATTERNS.some((p) => p.test(f)));
  } catch {
    return [];
  }
}

// 保存する。追跡しない状態ディレクトリと、名前で決まる追跡しない状態のファイルだけを tar にする
export function packState(repoDir: string, archive: string, run: Exec = exec, paths: string[] = STATE_PATHS): void {
  const present = paths.filter((p) => {
    try {
      run("test", ["-e", p], repoDir);
      return true;
    } catch {
      return false;
    }
  });
  present.push(...untrackedStateFiles(repoDir, run));
  run("tar", ["-czf", archive, "--", ...(present.length > 0 ? present : ["--files-from=/dev/null"])], repoDir);
}

// 戻す。追跡するパスを含めば何も書かずに StateRejectedError
export function unpackState(repoDir: string, archive: string, run: Exec = exec, paths: string[] = STATE_PATHS): void {
  const entries = run("tar", ["-tzf", archive], repoDir).split("\n").filter(Boolean);
  const bad = trackedEntries(entries, trackedFiles(repoDir, run), paths);
  const outside = entries.filter((e) => e.startsWith("/") || e.split("/").includes(".."));
  if (bad.length > 0 || outside.length > 0) throw new StateRejectedError([...bad, ...outside]);
  run("tar", ["-xzf", archive], repoDir);
}
