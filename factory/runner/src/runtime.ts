// ジョブの中身の本物。git・gh・claude・NDF のスクリプトをコンテナの中で動かす。
// NDF はイメージに焼き込まず、NDF_MARKETPLACE を NDF_VERSION の ref で入れる（受け入れ条件 24、決定 3）
import { spawn } from "node:child_process";
import { mkdir, readFile, readdir, realpath, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import type { RunJobInstruction } from "@ndf-factory/contracts";
import type { JobRuntime } from "./main.ts";
import { claudeEnv, ROLE_REFRESH_MS, stsAssumeRole, writeRoleCredentials, type ModelCredential } from "./model.ts";
import { exec, packState, unpackState } from "./state.ts";
import {
  delegatedFromRecord,
  issueRequestFromLine,
  gateKindOf,
  gateRecordName,
  isSprintState,
  phaseChanged,
  rejectionDirective,
  reportResult,
  translateProgressLine,
  usageFromResult,
  usageFromStepLine,
  verdictSourcesOf,
} from "./translate.ts";

const POLL_MS = 2000;
// 無人の conductor に許す Tool。supervise_lib/claude.py の FULL_TOOLS（Skill を回すステップ）と同じ
const CONDUCTOR_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch";

function run(cmd: string, args: string[], opts: { cwd: string; env?: Record<string, string>; onLine?: (line: string) => void }): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd: opts.cwd, env: opts.env ?? (process.env as Record<string, string>), stdio: ["ignore", "pipe", "inherit"] });
    let buf = "";
    child.stdout.on("data", (chunk: Buffer) => {
      buf += chunk.toString("utf8");
      let i: number;
      while ((i = buf.indexOf("\n")) >= 0) {
        opts.onLine?.(buf.slice(0, i));
        buf = buf.slice(i + 1);
      }
    });
    child.on("error", reject);
    child.on("close", (code) => resolve(code ?? 1));
  });
}

// 正式版はタグ ndf--v<版>、開発版は <版>@<SHA> のコミット
export function ndfRef(version: string): { ref: string; version: string } {
  const at = version.indexOf("@");
  return at >= 0 ? { ref: version.slice(at + 1), version: version.slice(0, at) } : { ref: `ndf--v${version}`, version };
}

export async function createRuntime(instruction: RunJobInstruction, env: Record<string, string | undefined>, token: string): Promise<JobRuntime> {
  const work = env.RUNNER_WORK_DIR ?? "/work";
  const repoDir = join(work, "repo");
  const marketplace = env.NDF_MARKETPLACE ?? "devbasex/ai-plugins";
  let credential: ModelCredential | null = null;
  if (instruction.EXECUTION_MODE === "byok" && instruction.MODEL_CREDENTIAL_SECRET_ARN) {
    const out = await new SecretsManagerClient({}).send(new GetSecretValueCommand({ SecretId: instruction.MODEL_CREDENTIAL_SECRET_ARN }));
    credential = JSON.parse(out.SecretString ?? "null") as ModelCredential;
  }
  const sessionName = `ndf-${instruction.JOB_ID}`;
  const credentialDir = join(homedir(), ".ndf-byok");
  // gh と git の認証はジョブ専用の置き場へ書き、利用者の ~/.config/gh と ~/.gitconfig を変えない。
  // NDF の worktree もジョブの作業場所の下へ置き、代わりの判断の記録をこのジョブの分だけに絞れるようにする
  const jobHome = join(work, ".ndf-job");
  const gitConfig = join(jobHome, "gitconfig");
  const jobEnv: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) if (v !== undefined) jobEnv[k] = v;
  jobEnv.GH_CONFIG_DIR = join(jobHome, "gh");
  jobEnv.GIT_CONFIG_GLOBAL = gitConfig;
  jobEnv.NDF_WORKTREE_BASE = join(work, "worktrees");
  // 別のリポジトリへの起票は、NDF がこのファイルへ依頼の行を足し、ここで issue.requested にする（#2216）。
  // 作業のディレクトリ（repo）の外に置き、コミットに入れない
  const issueRequests = join(jobHome, "issue-requests.jsonl");
  jobEnv.NDF_ISSUE_REQUEST_FILE = issueRequests;
  const sh = (cmd: string, args: string[], opts: { cwd: string; env?: Record<string, string> }) => run(cmd, args, { env: jobEnv, ...opts });
  const childEnv = () => claudeEnv(jobEnv, instruction.EXECUTION_MODE, credential, stsAssumeRole, sessionName, credentialDir);
  // 版を固定して取った取得元。スクリプトは入れた版と同じこの写しから読む
  const ndfSource = join(work, "ndf-marketplace");
  const ndfScripts = () => join(ndfSource, "plugins/ndf/scripts");
  // ask_ledger.py の置き場（${NDF_MVV_STATE_DIR:-~/.local/state/ndf}/ask.jsonl）
  const askLedger = join(env.NDF_MVV_STATE_DIR ?? join(homedir(), ".local/state/ndf"), "ask.jsonl");

  return {
    async writeGitHubToken(token) {
      const dir = jobEnv.GH_CONFIG_DIR!;
      await mkdir(dir, { recursive: true, mode: 0o700 });
      await writeFile(join(dir, "hosts.yml"), `github.com:\n    oauth_token: ${token}\n    git_protocol: https\n`, { mode: 0o600 });
    },
    async clone(repo) {
      await mkdir(jobHome, { recursive: true, mode: 0o700 });
      // 利用者の git の設定（名前・メールなど）は読み込み、gh auth setup-git の書き込みだけをこのファイルへ受ける
      const xdg = env.XDG_CONFIG_HOME ?? join(homedir(), ".config");
      await writeFile(gitConfig, `[include]\n\tpath = ${join(xdg, "git/config")}\n\tpath = ${join(homedir(), ".gitconfig")}\n`, { mode: 0o600 });
      await sh("gh", ["auth", "setup-git"], { cwd: work });
      if ((await sh("git", ["clone", `https://github.com/${repo}.git`, repoDir], { cwd: work })) !== 0) throw new Error("取り込みに失敗した");
    },
    async installNdf(version) {
      const { ref, version: want } = ndfRef(version);
      const src = ndfSource;
      if ((await sh("git", ["clone", "--depth", "1", "--branch", ref, `https://github.com/${marketplace}.git`, src], { cwd: work })) !== 0) {
        // コミットはブランチとして clone できないため、取ってから切り替える
        await sh("git", ["clone", `https://github.com/${marketplace}.git`, src], { cwd: work });
        if ((await sh("git", ["checkout", ref], { cwd: src })) !== 0) throw new Error("NDF の版を取れない");
      }
      // 導入の名前（ndf@<name>）は取得元の marketplace.json の name で決まる
      const name = (JSON.parse(await readFile(join(src, ".claude-plugin/marketplace.json"), "utf8")) as { name?: unknown }).name;
      if (typeof name !== "string" || name === "") throw new Error("取得元の marketplace.json に name が無い");
      const pluginEnv = await childEnv();
      if ((await run("claude", ["plugin", "marketplace", "add", src], { cwd: repoDir, env: pluginEnv })) !== 0) throw new Error("NDF の取得元を足せない");
      if ((await run("claude", ["plugin", "install", `ndf@${name}`], { cwd: repoDir, env: pluginEnv })) !== 0) throw new Error("NDF を入れられない");
      const manifest = JSON.parse(await readFile(join(src, "plugins/ndf/.claude-plugin/plugin.json"), "utf8")) as { version: string };
      return manifest.version === want ? version : manifest.version;
    },
    async restoreState(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`状態を読めない（${res.status}）`);
      const archive = join(work, "state.tar.gz");
      await writeFile(archive, Buffer.from(await res.arrayBuffer()));
      unpackState(repoDir, archive);
    },
    async saveState(url) {
      const archive = join(work, "state.tar.gz");
      packState(repoDir, archive);
      const res = await fetch(url, { method: "PUT", body: await readFile(archive) });
      if (!res.ok) throw new Error(`状態を保存できない（${res.status}）`);
    },
    async recordAnswer(approvalRequestId) {
      const res = await fetch(new URL(`/run-jobs/approval-requests/${approvalRequestId}/answer`, new URL(instruction.INGEST_URL).origin), {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`承認の依頼の答えを読めない（${res.status}）`);
      // 形は factory/contracts/openapi/runs.yaml の ApprovalAnswer
      const answer = (await res.json()) as { kind: string; decision?: string; body?: string | null; gate?: string };
      if (answer.kind === "decision") return answer.body ?? null;
      // 書く先はスプリント状態ファイル（sprint-state.py init が作る。開発ループでは周回の state.json）。
      // 目録の sprint.json（supervise.py new）は別のファイルのため、形で見分ける。API から受けず、復元した状態から求める
      const sprint = await latestSprintState(join(repoDir, ".ndf"));
      if (!sprint) throw new Error("復元した状態にスプリント状態ファイルが無い");
      const gate = answer.gate ?? gateRecordName(answer.kind);
      const decision = answer.decision ?? "rejected";
      const args = [join(ndfScripts(), "sprint-state.py"), "gate", sprint, gate, "--by", "user", "--outcome", decision];
      args.push("--what", answer.body || "Factory の画面での利用者の答え"); // sprint-state.py gate は --what が要る
      if ((await sh("python3", args, { cwd: repoDir })) !== 0) throw new Error("承認の記録を書けない");
      // 差し戻しは状態ファイルを変えないため、conductor へ差し戻しと理由を明示して渡す
      return decision === "rejected" ? rejectionDirective(gate, answer.body) : null;
    },
    async runConductor(inst, directive, emit) {
      const prompt =
        inst.START === "new"
          ? `/ndf:development-workflow ${inst.ISSUES!.split(",").map((n) => `#${n}`).join(" ")}`
          : `前回の続きから進めてください。${directive ? `利用者の判断: ${directive}` : ""}`;
      const offsets = new Map<string, number>();
      // このジョブの記録だけを送る（ask.jsonl は利用者の全プロジェクトで共有される）。cwd は realpath で書かれる
      const root = await realpath(work);
      const ownRow = (row: Record<string, unknown>) => typeof row.cwd === "string" && (row.cwd === root || row.cwd.startsWith(`${root}/`));
      // 再開では復元したログの末尾から読む。前のジョブが送った行を新しい job_id で送り直さない
      if (inst.START === "resume") {
        for (const file of [...(await findFiles(join(repoDir, ".ndf"), "progress.jsonl")), askLedger]) {
          offsets.set(file, (await readFile(file, "utf8").catch(() => "")).length);
        }
      } else {
        offsets.set(askLedger, (await readFile(askLedger, "utf8").catch(() => "")).length);
      }
      // 起票の依頼は前のジョブが送った行を送り直さない
      offsets.set(issueRequests, (await readFile(issueRequests, "utf8").catch(() => "")).length);
      let plan: string | null = null;
      let lastText = "";
      const poll = async () => {
        for (const file of await findFiles(join(repoDir, ".ndf"), "progress.jsonl")) {
          const content = await readFile(file, "utf8").catch(() => "");
          const from = offsets.get(file) ?? 0;
          offsets.set(file, content.length);
          for (const raw of content.slice(from).split("\n").filter(Boolean)) {
            const line = JSON.parse(raw) as Record<string, unknown>;
            const name = file.split("/").slice(-2, -1)[0]!.replace(/-state$/, "");
            if (name !== plan) {
              plan = name;
              await emit(phaseChanged(name));
            }
            const body = translateProgressLine(line, inst.REPO);
            if (body) await emit(body);
            // supervise が別のプロセスで起動したステップの使用量（conductor の result には入らない）
            for (const u of usageFromStepLine(line)) await emit(u);
          }
        }
        // 代わりの判断の記録（ask_ledger.py の ask.jsonl の kind: decision の行）
        const content = await readFile(askLedger, "utf8").catch(() => "");
        const from = offsets.get(askLedger) ?? 0;
        offsets.set(askLedger, content.length);
        const rows = (text: string) => text.split("\n").filter(Boolean).map((raw) => JSON.parse(raw) as Record<string, unknown>);
        const decisions = rows(content.slice(from)).filter((row) => row.kind === "decision" && ownRow(row));
        // ref の判定の行は読み取り位置より前にもあるため、判断の行があるときだけ全体から引く
        const sources = decisions.length > 0 ? verdictSourcesOf(rows(content)) : new Map<string, string>();
        for (const row of decisions) {
          const body = delegatedFromRecord(row, sources);
          if (body) await emit(body);
        }
        // 別のリポジトリへの起票の依頼（NDF_ISSUE_REQUEST_FILE の 1 行 1 件）
        const requests = await readFile(issueRequests, "utf8").catch(() => "");
        const fromRequest = offsets.get(issueRequests) ?? 0;
        offsets.set(issueRequests, requests.length);
        for (const row of rows(requests.slice(fromRequest))) {
          const body = issueRequestFromLine(row);
          if (body) await emit(body);
        }
      };
      // 送り始めた事象は、返す前にすべて待ち合わせる（返した後に親が job.finished を送って終わるため）
      const pending = new Set<Promise<void>>();
      const track = (p: Promise<void>) => {
        const q = p.catch(() => undefined).finally(() => pending.delete(q));
        pending.add(q);
      };
      let polling: Promise<void> | null = null;
      const timer = setInterval(() => {
        if (polling) return;
        polling = poll()
          .catch(() => undefined)
          .finally(() => {
            polling = null;
          });
      }, POLL_MS);
      // ロールの資格は 1 時間で切れるため、動いている間は置き直す（子は credential_process で読み直す）
      const role = credential?.kind === "aws_role" ? credential : null;
      const refresher = role
        ? setInterval(() => void writeRoleCredentials(role, stsAssumeRole, sessionName, credentialDir).catch(() => undefined), ROLE_REFRESH_MS)
        : null;
      const args = ["-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--allowed-tools", CONDUCTOR_TOOLS];
      const code = await run("claude", args, {
        cwd: repoDir,
        env: await childEnv(),
        onLine: (raw) => {
          try {
            const msg = JSON.parse(raw) as Record<string, unknown>;
            if (msg.type === "result") {
              lastText = typeof msg.result === "string" ? msg.result : lastText;
              for (const u of usageFromResult(msg, "conductor")) track(emit(u));
            }
          } catch {
            // stream-json でない行は読まない
          }
        },
      });
      clearInterval(timer);
      if (refresher) clearInterval(refresher);
      await polling;
      await poll();
      await Promise.all(pending);
      if (code !== 0) throw new Error(`conductor が ${code} で終わった`);
      const report = reportResult(lastText);
      const prUrl = /https:\/\/github\.com\/[^\s)]+\/pull\/\d+/.exec(lastText)?.[0] ?? null;
      return { report, summary: lastText.slice(0, 2000), gate: report === "判断が要る" ? { kind: gateKindOf(plan), pr_url: prUrl, material: lastText } : null };
    },
  };
}

// スプリント状態ファイルのうち最後に更新したもの。名前は呼ぶ側が決めるため、よく使う名前を集めて形で選ぶ
async function latestSprintState(ndfDir: string): Promise<string | null> {
  const names = ["state.json", "sprint-state.json", "sprint.json"];
  const found = (await Promise.all(names.map((n) => findFiles(ndfDir, n)))).flat();
  let best: { path: string; mtime: number } | null = null;
  for (const path of found) {
    const json = await readFile(path, "utf8").then((t) => JSON.parse(t) as unknown).catch(() => null);
    if (!isSprintState(json)) continue;
    const mtime = (await stat(path)).mtimeMs;
    if (!best || mtime > best.mtime) best = { path, mtime };
  }
  return best?.path ?? null;
}

async function findFiles(dir: string, name: string, depth = 5): Promise<string[]> {
  if (depth < 0) return [];
  const out: string[] = [];
  for (const entry of await readdir(dir).catch(() => [] as string[])) {
    const path = join(dir, entry);
    const s = await stat(path).catch(() => null);
    if (!s) continue;
    if (s.isDirectory()) out.push(...(await findFiles(path, name, depth - 1)));
    else if (entry === name) out.push(path);
  }
  return out.sort();
}

export { exec };
