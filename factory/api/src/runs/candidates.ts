// 課題の候補（F4、決定 9）。棚卸しの順位の表か新しい順の課題を読み、サービスの Bedrock の claude で採否と理由を作る。
// 費用は使える量に数えず、ログに 1 行残す。候補は保存しない
import { BedrockRuntimeClient, ConverseCommand } from "@aws-sdk/client-bedrock-runtime";
import type { AccountContext } from "../accounts/model.ts";
import { NotFoundError } from "../errors.ts";
import type { GitHubPort, IssueSummary } from "../github/port.ts";
import { MAX_ISSUES } from "./control.ts";
import type { RunsTable } from "./table.ts";

export const RECENT_LIMIT = 10;
export const CANDIDATE_MAX_TOKENS = 1500;
const DEFAULT_SELECT = 5;

export interface CandidateChoice {
  number: number;
  selected: boolean;
  reason: string;
}

// 課題の並びから採否と理由を作る。失敗は投げる
export type CandidateModel = (source: "ranking" | "recent", issues: IssueSummary[]) => Promise<CandidateChoice[]>;

export interface Candidate {
  number: number;
  title: string;
  selected: boolean;
  reason: string;
}

export interface CandidateList {
  source: "ranking" | "recent";
  milestone: string | null;
  candidates: Candidate[];
  note: string | null;
}

export const CANDIDATE_FALLBACK_NOTE = "AI が理由を作れなかった。順位の表の順に並べています";

export async function chooseCandidates(
  deps: { table: RunsTable; github: GitHubPort; model: CandidateModel },
  ctx: AccountContext,
  repositoryId: number,
  exclude: number[],
): Promise<CandidateList> {
  const reg = await deps.table.getRegistration(ctx.accountId, repositoryId);
  if (!reg) throw new NotFoundError("repository_not_found", "登録したリポジトリが見つかりません");
  const skip = new Set(exclude);
  const ranking = await deps.github.findRankingTable(reg.installationId, reg.repositoryId, reg.fullName);
  const source = ranking ? "ranking" : "recent";
  const pool = (ranking ? ranking.rows : await deps.github.listRecentOpenIssues(reg.installationId, reg.repositoryId, reg.fullName, RECENT_LIMIT + skip.size))
    .filter((i) => !skip.has(i.number))
    // 順位の表も上限で切る（出力の CANDIDATE_MAX_TOKENS に収まる件数。選べる数の上限と同じ）
    .slice(0, ranking ? MAX_ISSUES : RECENT_LIMIT);
  const milestone = ranking?.milestone ?? null;
  if (pool.length === 0) return { source, milestone, candidates: [], note: "候補にできる開いた課題がありません" };
  try {
    const choices = new Map((await deps.model(source, pool)).map((c) => [c.number, c]));
    const candidates = pool.map((i) => {
      const c = choices.get(i.number);
      if (!c || c.reason.trim() === "") throw new Error("理由の無い候補");
      return { number: i.number, title: i.title, selected: c.selected, reason: c.reason };
    });
    return { source, milestone, candidates, note: null };
  } catch {
    const label = source === "ranking" ? "順位の表" : "開いている課題の新しい順";
    return {
      source,
      milestone,
      candidates: pool.map((i, n) => ({ number: i.number, title: i.title, selected: n < DEFAULT_SELECT, reason: `${label}の ${n + 1} 番目` })),
      note: CANDIDATE_FALLBACK_NOTE,
    };
  }
}

// Bedrock の Converse で採否と理由を作る。入力は順位の行と題だけ、出力は CANDIDATE_MAX_TOKENS まで
export function bedrockCandidateModel(modelId: string, client = new BedrockRuntimeClient({}), log = (e: object) => console.log(JSON.stringify(e))): CandidateModel {
  return async (source, issues) => {
    const lines = issues.map((i, n) => `${n + 1}. #${i.number} ${i.title}`).join("\n");
    const prompt =
      `次は${source === "ranking" ? "棚卸しの順位の表" : "開いている課題（新しい順）"}です。次のスプリントに入れる課題を選び、` +
      `各課題に selected（真偽）と reason（日本語 1 文）を付けた JSON の配列 [{"number":番号,"selected":真偽,"reason":"理由"}] だけを返してください。\n${lines}`;
    const out = await client.send(
      new ConverseCommand({
        modelId,
        messages: [{ role: "user", content: [{ text: prompt }] }],
        inferenceConfig: { maxTokens: CANDIDATE_MAX_TOKENS },
      }),
    );
    log({ event: "issue_candidates", input_tokens: out.usage?.inputTokens ?? 0, output_tokens: out.usage?.outputTokens ?? 0 });
    const text = out.output?.message?.content?.map((c) => c.text ?? "").join("") ?? "";
    const json = text.slice(text.indexOf("["), text.lastIndexOf("]") + 1);
    const parsed = JSON.parse(json) as unknown[];
    return parsed.map((p) => {
      const o = p as Record<string, unknown>;
      return { number: Number(o.number), selected: o.selected === true, reason: String(o.reason ?? "") };
    });
  };
}
