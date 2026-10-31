// ジョブの中身の本物。git・gh・claude・NDF のスクリプトをコンテナの中で動かす。
// NDF はイメージに焼き込まず、NDF_MARKETPLACE を NDF_VERSION の ref で入れる（受け入れ条件 24、決定 3）
import { spawn } from "node:child_process";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import type { RunJobInstruction } from "@ndf-factory/contracts";
import type { JobRuntime } from "./main.ts";
import { claudeEnv, stsAssumeRole, type ModelCredential } from "./model.ts";
import { exec, packState, unpackState } from "./state.ts";
import { delegatedFromRecord, gateKindOf, phaseChanged, reportResult, translateProgressLine, usageFromResult } from "./translate.ts";

const POLL_MS = 2000;

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
  const childEnv = () => claudeEnv(env, instruction.EXECUTION_MODE, credential, stsAssumeRole, `ndf-${instruction.JOB_ID}`);
  const ndfScripts = () => join(homedir(), ".claude/plugins/marketplaces/ndf-pinned/plugins/ndf/scripts");

  return {
    async writeGitHubToken(token) {
      const dir = join(homedir(), ".config/gh");
      await mkdir(dir, { recursive: true });
      await writeFile(join(dir, "hosts.yml"), `github.com:\n    oauth_token: ${token}\n    git_protocol: https\n`, { mode: 0o600 });
    },
    async clone(repo) {
      await mkdir(work, { recursive: true });
      await run("gh", ["auth", "setup-git"], { cwd: work });
      if ((await run("git", ["clone", `https://github.com/${repo}.git`, repoDir], { cwd: work })) !== 0) throw new Error("取り込みに失敗した");
    },
    async installNdf(version) {
      const { ref, version: want } = ndfRef(version);
      const src = join(work, "ndf-marketplace");
      if ((await run("git", ["clone", "--depth", "1", "--branch", ref, `https://github.com/${marketplace}.git`, src], { cwd: work })) !== 0) {
        // コミットはブランチとして clone できないため、取ってから切り替える
        await run("git", ["clone", `https://github.com/${marketplace}.git`, src], { cwd: work });
        if ((await run("git", ["checkout", ref], { cwd: src })) !== 0) throw new Error("NDF の版を取れない");
      }
      await run("claude", ["plugin", "marketplace", "add", src], { cwd: repoDir, env: await childEnv() });
      await run("claude", ["plugin", "install", "ndf@ndf-pinned"], { cwd: repoDir, env: await childEnv() });
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
      const answer = (await res.json()) as { kind: string; decision?: string; body?: string; sprint?: string; gate?: string };
      if (answer.kind === "decision") return answer.body ?? null;
      const args = [join(ndfScripts(), "sprint-state.py"), "gate", answer.sprint ?? "", answer.gate ?? answer.kind, "--by", "user", "--outcome", answer.decision ?? "rejected"];
      if (answer.body) args.push("--what", answer.body);
      if ((await run("python3", args, { cwd: repoDir })) !== 0) throw new Error("承認の記録を書けない");
      return null;
    },
    async runConductor(inst, directive, emit) {
      const prompt =
        inst.START === "new"
          ? `/ndf:development-workflow ${inst.ISSUES!.split(",").map((n) => `#${n}`).join(" ")}`
          : `前回の続きから進めてください。${directive ? `利用者の判断: ${directive}` : ""}`;
      const offsets = new Map<string, number>();
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
          }
        }
        // #2206 の代わりの判断の記録。置き場は #2206 が決める（NDF_DELEGATED_FILE）。無ければ何も送らない
        const delegated = env.NDF_DELEGATED_FILE ? join(repoDir, env.NDF_DELEGATED_FILE) : null;
        if (delegated) {
          const content = await readFile(delegated, "utf8").catch(() => "");
          const from = offsets.get(delegated) ?? 0;
          offsets.set(delegated, content.length);
          for (const raw of content.slice(from).split("\n").filter(Boolean)) {
            const body = delegatedFromRecord(JSON.parse(raw) as Record<string, unknown>);
            if (body) await emit(body);
          }
        }
      };
      const timer = setInterval(() => void poll().catch(() => undefined), POLL_MS);
      const code = await run("claude", ["-p", prompt, "--output-format", "stream-json", "--verbose"], {
        cwd: repoDir,
        env: await childEnv(),
        onLine: (raw) => {
          try {
            const msg = JSON.parse(raw) as Record<string, unknown>;
            if (msg.type === "result") {
              lastText = typeof msg.result === "string" ? msg.result : lastText;
              for (const u of usageFromResult(msg, "conductor")) void emit(u);
            }
          } catch {
            // stream-json でない行は読まない
          }
        },
      });
      clearInterval(timer);
      await poll();
      if (code !== 0) throw new Error(`conductor が ${code} で終わった`);
      const report = reportResult(lastText);
      const prUrl = /https:\/\/github\.com\/[^\s)]+\/pull\/\d+/.exec(lastText)?.[0] ?? null;
      return { report, summary: lastText.slice(0, 2000), gate: report === "判断が要る" ? { kind: gateKindOf(plan), pr_url: prUrl, material: lastText } : null };
    },
  };
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
