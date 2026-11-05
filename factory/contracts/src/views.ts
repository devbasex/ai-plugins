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
