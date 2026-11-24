# cross-review: 1 ラウンドを 2 つの席で読み、空いた席を claude の読むだけの起動で埋める

**cross-review の 1 ラウンドは 2 つの席で読む。** 相手役が欠けたり、使ってよいランタイムの設定（`.ndf/runtimes.json` の
`allowed`）のために足りなかったりして席が空けば、修正担当とは別のプロセスで動く claude の読むだけの起動で埋める（#3955）。
規則は相手役を決める部品（`ndf.core.converge.participants`）が持ち、cross-refactoring の相手役の選び方（`assign`）は変えない。

手順と引数の表は [`cross-review` の SKILL.md](../../plugins/ndf/skills/cross-review/SKILL.md) と
[状態とレビューの手順](../../plugins/ndf/skills/cross-review/docs/01-state-and-review.md) が正である。この文書は決定の理由と部品の契約を持つ。
10 系（v10.27.77 まで）の参加者プールとスロットの仕組みは、[10 系の参加者プールとスロット](cross-review-participants-and-seats-10x.md)に記録として残す。

## 例

kiro が未ログインの環境で、相手役の既定（codex・kiro）のまま `ndf cross-review run` を打つ。

1. round 1 で kiro が読めず、1 度起動し直しても読めない。codex は指摘を返した
2. kiro の席を claude の読むだけの起動（席の名前 `claude`）で埋め、`--base` からの全差分を読ませる。判定は codex と claude の指摘で行う
3. round 2 以降は codex と claude を起動し、kiro は起動しない
4. 状態ファイルのラウンドの記録は `"reviewers": ["codex", "claude"]`・`"filled": [{"seat": "claude", "for": "kiro", "reason": "failed: 終了コード 1"}]` になり、
   記録のコメントのラウンドの表の「相手役」の列は `codex・claude（kiro の席）`、結果 JSON の `warnings` に埋めたことと理由が載る

## 席の決め方（起動の前）

`review_seats(requested, allowed)` が決める。`run` と `review-scope` は同じ関数を使う。

| 入力 | 席 | 以後に埋めてよいか |
| --- | --- | --- |
| 名指し無し・`allowed` 無し | codex・kiro | 真 |
| 名指し無し・`allowed` が claude と codex | codex・claude（`filled` の `reason` は `allowed の相手役 1（codex）`、`for` は null） | 真 |
| 名指し無し・`allowed` が claude だけ | claude・claude-2（`reason` は `allowed の相手役 0`） | 真 |
| 名指しが 2 者以上 | 重なりと修正担当を除いた名指しの並び | 真 |
| 名指しが 1 者（`--reviewer codex --reviewer codex` も 1 者） | その 1 者 | 偽 |

修正担当が `allowed` の外なら `FixerNotAllowed`（`precondition`）、名指しが読むだけの起動を持たないか `allowed` の外なら
`UnknownParticipant`（`invalid`）で止まる。席の名前からランタイムを引くのは `runtime_of` だけで、`claude-<数>` は claude である。
起動・無進捗の許容・利用上限の検知・始める前の確かめ・直し方はランタイムの名前で、指摘の担当・出力の名前・記録・注意は席の名前で扱う。

## 席の埋め方

| 時点 | 条件 | 扱い | `filled` の `reason` |
| --- | --- | --- | --- |
| 起動の前 | `allowed` の中の相手役が 2 に満たない（名指し無し） | 足りない席を `claude`・`claude-2` で埋める | `allowed の相手役 <数>（<名前>）` |
| 始める前の確かめ | 学習の設定で claude 以外の相手役が断られた（名指し 1 者を除く） | 断られた相手役を起動せず、その席を埋める。修正担当か名指し 1 者が断られたら `precondition` | `unready: <理由>` |
| ラウンドの中 | claude 以外の席だけが起動し直しても結果を返さない | 同じラウンドで 1 回ずつ埋め、全差分と前回の情報を含まないリクエストを渡す | `<状態>: <詳細>`（サンドボックスの失敗はその文） |

- 起動し直すのは claude 以外の席だけである
- 起動し直した席も含め、結果を返した席が 1 つも無ければ、席を埋めずに `precondition` で止まる（差分や環境の側の原因が多く、claude でも読めないことがある）
- 少なくとも 1 席が結果を返し、claude の席が結果を返さなかった場合は、席を埋めずに `incomplete_reviewers` で止まる
- 埋めた claude も結果を返さなければ、埋め直さずに `incomplete_reviewers` で止まる
- 埋めた席は同じ実行の以後のラウンドも座り続け、`filled` は埋めた後のすべてのラウンドに書く
- 同じ head の打ち直しは、前回の席に今回の席を 1 対 1 で割り当てて前回の結果を返せるかを決める。埋めた席は、席の名前か欠けた
  相手役のどちらか一方に割り当てる（`scope_decide.assignable`）。名指しが 1 者なら席の名前だけで比べる

## 席の claude の起動

| 項目 | 値 | 理由 |
| --- | --- | --- |
| 構成 | 会話を残さない最小構成の `claude -p`（`participants.read_only_launch`）。cross-refactoring の読むだけの起動と同じ関数 | 修正担当の会話を引き継がない。同じ役割の起動を 2 か所に書かない |
| Tool | `--tools`・`--allowed-tools` とも `Read,Grep,Glob` | ファイルを書き換えない |
| 読める場所 | `--restricted`・`--add-dir <その実行の review-input-*>`・`--permission-mode dontAsk` | 席の指摘は PR のコメントに載る。差分に仕込まれた指示で worktree の外を読み、書き写すことを起動の形で断る |
| 資料のディレクトリ | 実行ごとに `tempfile.mkdtemp(prefix="review-input-", dir=<保存先>)` で作り、終わり（止まったときを含む）に消す | 前の実行（別の PR）の資料を読ませない。保存先の直下（修正担当の結果・ログ・状態ファイル）は読ませない |
| バージョンの下限 | `--restricted` を含む起動だけ claude 2.1.316（`cli-floors.json` の `flag_floors`）。バージョンを確かめられなければ起動しない | 2.1.315 以前は `--restricted` でも作業ディレクトリの外のファイルを送りうる。共通の下限（2.1.268）は上げず、席を埋めない実行を止めない |

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 席を埋める規則を `review_seats` として足し、`assign` は変えない | cross-refactoring の選び方を引数の既定値に預けない |
| 空いた席は残った相手役の 2 つ目ではなく claude で埋める | #1598 の計測で 2 者は互いの見逃しを拾い合っていた。利用者の問い「その場合 codex と claude になるのでは？」（2026-11-21） |
| 埋めるのは起動し直しの後で、claude の席は起動し直さない | 一時的な失敗で claude の利用を増やさない。claude の失敗は利用上限や認証のように起動し直しで直らないものが多い |
| 名指しが 1 者のときだけ埋めない | 利用者が 1 者と決めた指定を尊重し、2 者の名指しは既定と同じ強さのレビューを保つ |
| 埋めた席を実行の間の今の席として持ち、記録に実際の席を書く | 同じ失敗を毎ラウンド待たない。前の head からの差分を渡してよいかを、実際に読んだ席で判定する |

## 関連リンク

- [issue #3955](https://github.com/devbasex/ai-plugins/issues/3955) — 空いた席を claude の読むだけの起動で埋める（11 系）
- [10 系の参加者プールとスロット](cross-review-participants-and-seats-10x.md) — v10.27.77 までの仕組みの記録（この文書から分けた）
- [ラウンドで担当が受け取るもの](cross-review-round-inputs.md) — 出し切りの指示・スナップショットの取り直し・設計 Pull Request の観点（#542 #786）
- [`cross-review` の状態とレビューの手順](../../plugins/ndf/skills/cross-review/docs/01-state-and-review.md)
- [`cross-review` の手順](../../plugins/ndf/skills/cross-review/SKILL.md)
- [証拠ベースのレビューと効果の測定](cross-review-evidence-based.md) — 指摘の構造化・実行検証・反証と、状態ファイルのそれ以外の鍵
- [起動結果と上限の検知](cross-review-launch-outcome.md) — 結果なしの理由の語彙とリトライ可否
