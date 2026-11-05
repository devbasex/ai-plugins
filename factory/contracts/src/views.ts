// 画面の応答の形のうち、API と画面の両方が書く形（#2172）。正はここだけにある。
// 画面も読むため、この入口（@ndf-factory/contracts/views）は依存を持たない

// 工程の中身（GET .../steps/{key}）の本体。工程の行（StepRow）に足す項目
export interface StepDetailBody {
  files: { path: string; added: number | null; deleted: number | null; url: string | null }[] | null;
  files_omitted: number | null;
  commit_range: { from: string; to: string } | null;
  decisions: { question: string; answer: string; by: "ai" | "user" }[] | null;
  output: { kind: "report" | "log"; text: string; truncated: boolean } | null;
  diff_url: string | null;
}

// スプリントの詳細の注意の行（#2430）。進捗の事象の attention を記録された順（order_key の順）に写す
export interface AttentionRow {
  // 事象の idempotency_key
  key: string;
  at: string;
  // 受け口が受けた理由の値（base_conflict など）。値の集合は契約で閉じていない
  reason: string;
  summary: string;
}

// 承認待ちの一覧の行（#2430）。1 つの実行に 0 か 1 行で、答えの無い最後の gate.reached から作る
export interface ApprovalRequestRow {
  sprint_run_id: string;
  repository_id: number;
  // owner/name
  repo: string;
  issues: number[];
  kind: "gate1" | "gate2" | "decision";
  // 進捗の事象の LoopRef。ループでない実行は null
  loop: { name: string; lap: number } | null;
  // 最後の gate.reached の日時
  waiting_since: string;
}
