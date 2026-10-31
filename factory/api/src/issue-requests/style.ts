// 相手先の流儀の判定を呼ぶ口（#2216 の決定 8）。判定そのものは #2239 が持ち、同じ型の中身を替える。
// ここは GitHub Issues 以外を返したときに下書きへ回す扱いだけを持つ

// 起票先（登録から引いた値）
export interface FilingTarget {
  repo: string;
  installationId: number;
  repositoryId: number;
}

// tracker が github_issues でなければ起票しない（unsupported_tracker）。template は Issue テンプレートの名前
export interface FilingStyle {
  tracker: string;
  template: string | null;
}

export interface FilingStyleResolver {
  resolve(target: FilingTarget): Promise<FilingStyle>;
}

// #2239 が入るまでの既定: GitHub Issues へ、テンプレートを使わず、依頼の題と本文のまま
export const defaultFilingStyle: FilingStyleResolver = {
  resolve: async () => ({ tracker: "github_issues", template: null }),
};
