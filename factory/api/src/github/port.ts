// GitHub の口（腐敗防止層）。取り込み・実行の制御・課題の候補・ランナー向けの API はこの型だけを使い、
// テストは偽物に替える。App の秘密鍵はこの口の実装だけが読む（決定 7）

// 利用者が導入へ届くリポジトリ（登録の候補）
export interface ReachableRepository {
  repositoryId: number;
  fullName: string;
  installationId: number;
}

export interface InstallationToken {
  token: string;
  expiresAt: string;
}

export interface IssueSummary {
  number: number;
  title: string;
}

// 棚卸しの順位の表（/ndf:backlog-refinement がマイルストーンの説明へ書く表）。題は GitHub から引いて埋める
export interface RankingTable {
  milestone: string;
  rows: IssueSummary[];
}

// 説明から拾った順位の表。題を持たず、マイルストーンの番号で課題を引く
export interface RankedMilestone {
  milestone: string;
  milestoneNumber: number;
  numbers: number[];
}

// 課題の番号を確かめた結果。開いている課題でない番号（閉じた・Pull Request・無い）を返す
export interface IssueCheck {
  invalid: number[];
}

export interface GitHubPort {
  installUrl(): string;
  authorizeUrl(state: string): string;
  // 利用者の認可の code を利用者の token へ換える。換えられなければ null
  exchangeCode(code: string): Promise<string | null>;
  // 利用者の token で、利用者が届く導入とその対象のリポジトリを引く（決定 12）
  listReachableRepositories(userToken: string): Promise<ReachableRepository[]>;
  // そのリポジトリ 1 つに絞った導入の token（権限は決定 6 の表）。導入が届かなければ null
  createInstallationToken(installationId: number, repositoryId: number): Promise<InstallationToken | null>;
  // 導入の token でリポジトリの今の名前を引く。届かなければ null
  repositoryName(installationId: number, repositoryId: number): Promise<string | null>;
  checkOpenIssues(installationId: number, fullName: string, numbers: number[]): Promise<IssueCheck>;
  listRecentOpenIssues(installationId: number, fullName: string, limit: number): Promise<IssueSummary[]>;
  findRankingTable(installationId: number, fullName: string): Promise<RankingTable | null>;
}

// マイルストーンの名前の形（<2 桁の連番> <主題>）
const MILESTONE = /^(\d{2}) \S/;
// 順位の表の行（| 順位 | #番号（判断待ち） | UBV | …。upkeep_rank_table.py の形）から番号を拾う。表は題を持たない
const RANK_ROW = /^\|\s*\d+\s*\|\s*#(\d+)[^|]*\|/;

// 開いたマイルストーンのうち、順位の表を持つ番号が最も小さいものから表を作る
export function pickRankingTable(milestones: { title: string; number: number; description: string | null }[]): RankedMilestone | null {
  const ordered = milestones
    .map((m) => ({ ...m, order: MILESTONE.exec(m.title)?.[1] }))
    .filter((m): m is typeof m & { order: string } => m.order !== undefined)
    .sort((a, b) => Number(a.order) - Number(b.order));
  for (const m of ordered) {
    const numbers = (m.description ?? "")
      .split("\n")
      .map((line) => RANK_ROW.exec(line.trim()))
      .filter((x): x is RegExpExecArray => x !== null)
      .map((x) => Number(x[1]));
    if (numbers.length > 0) return { milestone: m.title, milestoneNumber: m.number, numbers };
  }
  return null;
}
