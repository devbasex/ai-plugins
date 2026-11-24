# cross-review: 1 ラウンドを 2 つの席で読み、空いた席を claude の読むだけの起動で埋める

**cross-review の 1 ラウンドは 2 つの席で読む。** 相手役が欠けたり、使ってよいランタイムの設定（`.ndf/runtimes.json` の
`allowed`）のために足りなかったりして席が空けば、修正担当とは別のプロセスで動く claude の読むだけの起動で埋める（#3955）。
規則は相手役を決める部品（`ndf.core.converge.participants`）が持ち、cross-refactoring の相手役の選び方（`assign`）は変えない。
席の起動と埋める処理は `ndf.domains.crossreview.seats`、埋めた席の記録と照合は `scope_decide`、記録のコメントの列は `report_decide` が持つ。

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

## 用語

| 用語 | 意味 |
| --- | --- |
| 席 | 1 ラウンドの 2 つのレビュー担当の枠。席の名前は相手役の名前（`codex`・`kiro`・`claude`）で、claude の 2 つ目の席は `claude-2`（3 つ目以降は `claude-<数>`）と呼ぶ。10 系の [#1598](https://github.com/devbasex/ai-plugins/issues/1598) の名前を引き継ぐ |
| 結果が無い | 席がレビュー結果を返さなかったこと。完了（`AgentState.DONE`）でない、出力から `findings` 配列を取り出せない、または claude の席が渡した差分のファイルの読み取りを拒否された（`permission_denials`）場合を指す。`{"findings": []}` で完了した席は結果がある |
| 埋める | 空いた席に、修正担当と別のプロセスで claude の読むだけの起動を座らせること。状態ファイルの `filled` に 1 件として残る |

## 常に成り立つ条件

| 条件 | 破れそうなときの扱い |
| --- | --- |
| 1 者だけを名指したときを除き、判定に使うラウンドは 2 つ以上の席の指摘を持つ | 1 席しか読めなければ、そのラウンドを収束に数えず `incomplete_reviewers` で止まる |
| 埋めるのは claude 以外の席だけで、1 つの席を埋めるのは 1 回だけ。claude の席は起動し直さない | 結果の無い席に claude の席が含まれれば、どの席も埋めずに `incomplete_reviewers` で止まる |
| 全員が結果を返さなかったラウンドでは席を埋めない | `precondition` で止め、そのラウンドを状態ファイルに残さない |
| 一度埋めた席には、同じ実行の以後のラウンドも埋めた claude が座る | 駆動が今の席（`_Run.seats`）を持ち続けて保つ |
| 席の claude は修正担当と会話を共有せず、書き換える Tool を持たず、読めるのはレビューする worktree と資料のディレクトリだけ | 起動の引数で保つ（「席の claude の起動」） |

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

### 席の名前とランタイムの名前

| 名前 | 値 | 使う所 |
| --- | --- | --- |
| 席の名前 | 相手役の名前と `claude-<数>` | 指摘の担当（`by`）・出力のファイルの名前（`round<n>-<席>`）・ラウンドの記録・注意 |
| ランタイムの名前 | `runtime_of(席)`（`claude-<数>` は `claude`） | 起動（`agents.Launch.agent`）・無進捗の許容・利用上限の検知・始める前の確かめ・読めなかったときの直し方 |

起動に席の名前を渡すと、`claude-2` は無進捗の許容（`agents.stall_seconds` が引く `monitor.stall.<担当>`）に無く、claude の許容ではなく既定の許容で止まり、
利用上限の検知も外れる。席の名前をランタイムの名前のままにすると、claude の席が 2 つのときに出力のファイルと指摘の担当が重なる。

### 読み取りの境界

**範囲の外の読み取りは、許可の規則ではなく起動の形（`--restricted`・`--add-dir`・`dontAsk`）で断る。** 席の claude の指摘は
PR のコメントとして公開され、差分や PR の本文に仕込まれた指示で worktree の外（`~/.ssh`・認証情報など）を読んで書き写すと、
その中身が開示されるためである。codex と kiro はサンドボックスの中で動くが、claude の読むだけの起動にはこの引数のほかに境界が無い。

- 許可の規則（`--allowed-tools` に場所を限った `Read(...)`）は当たらない操作を断らない。拒否の規則（`--disallowed-tools`）は並べ漏れた場所を読ませる。どちらも採らない
- `--tools` と `--allowed-tools` には Tool の名前（`Read,Grep,Glob`）だけを渡す。読める場所は `adapters/claude.py` の `ClaudeCall` の一覧で受け、空でないときだけ `permission_args` が 3 つの引数を組む。cross-refactoring は場所を渡さないので、起動の引数は変わらない
- 資料（相手役へ渡す差分・前の head からの差分・既存のコメント・埋めた claude 用の全差分）は、実行ごとに `mkdtemp` で作る `review-input-<接尾辞>/` に書き、依頼文はその中のファイルだけを指す。`mkdtemp` は既存の同名のディレクトリやリンクを使わず、作った利用者だけが読める空のディレクトリを作る。codex と kiro にも同じパスを渡す
- 保存先の全体を `--add-dir` に渡さない。修正担当の結果・実行のログ・状態ファイルまで読めるようになるためである
- 資料のディレクトリは実行の終わりに消えるので、止まった後に保存先から資料を見返すことはできない
- 席の claude が渡した差分のファイルの読み取りを拒否された（利用者の permissions の deny など）ときは、指摘が空でも結果が無い席として扱い、理由と直し方（読み取りの範囲と deny を確かめる）を載せる。読めないまま「指摘なし」で収束させないためである

**`--restricted` を含む起動にだけ、claude の下限 2.1.316 を当てる（`cli-floors.json` の claude の行の `flag_floors`）。**

- 2.1.315 以前は、読み取りの許可の規則が当たると `--restricted` のセッションでも作業ディレクトリの外のファイルをアップロードしうる不具合があった（[Claude Code の変更履歴の 2.1.316](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#21316)）。席の起動は `--allowed-tools` に `Read` を渡すので当たる
- `cli_floor.require(name, argv0, env, cwd, args)` は起動の引数に `flag_floors` の引数があればその下限と比べ、版を確かめられなければ `Unverified`（`TooOld` の派生）で断る。境界の不具合を含む版を通さないためである。`agents._guarded_run` は例外を `refused` にし、その席は結果が無い席として扱う
- `flag_floors` の引数を含まない起動は、今のとおり `min_version`（2.1.268）と比べ、版を読めなければ通す
- 共通の下限を上げないのは、席を埋めない実行を止めないためである。上げると修正担当の claude も 2.1.268〜2.1.315 で断られ、codex と kiro が両方読める実行まで止まる。古い claude の環境では、席を埋める場面でだけ席の claude が断られ、`incomplete_reviewers` か `precondition` で止まる

**実機確認の結果（claude 2.1.331、2026-11-24）。** 席の claude と同じ引数（出力の形だけ `stream-json --verbose`）で `claude -p` を起動し、
`Read`・`Grep`・`Glob` のそれぞれについて、範囲の中・範囲の外を直接指す・範囲の中のリンクを通して外を指す、の 19 対象を 1 回ずつ
試した。判定は回答の文ではなく `tool_use` の引数と `tool_result` で行った。

| 区分 | 対象の数 | 結果 |
| --- | --- | --- |
| 範囲の中（資料のディレクトリ・worktree） | 5 | すべて目印か差分のファイルの名前を返した |
| 範囲の外を直接指す（外の一時ディレクトリ・保存先の直下） | 6 | すべて拒否（`is_error`、`<path> is outside ...; --restricted confines the file tools to the working directory.`） |
| 範囲の中のリンクを通す | 8 | 6 は拒否（リンクの先を解決してから判定する）。`Glob` の `link-to-outside/*` の 2 は拒否でなく `No files found` で、名前は出なかった |

範囲の外の内容も名前も 1 つも取れなかった。手順と試行ごとの表は [PR #4461](https://github.com/devbasex/ai-plugins/pull/4461) の本文にある。

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 席を埋める規則を `review_seats` として足し、`assign` は変えない | cross-refactoring の選び方を引数の既定値に預けない |
| 空いた席は残った相手役の 2 つ目ではなく claude で埋める | #1598 の計測で 2 者は互いの見逃しを拾い合っていた。利用者の問い「その場合 codex と claude になるのでは？」（2026-11-21） |
| 埋めるのは起動し直しの後で、claude の席は起動し直さない | 一時的な失敗で claude の利用を増やさない。claude の失敗は利用上限や認証のように起動し直しで直らないものが多い |
| 名指しが 1 者のときだけ埋めない | 利用者が 1 者と決めた指定を尊重し、2 者の名指しは既定と同じ強さのレビューを保つ |
| 埋めた席を実行の間の今の席として持ち、記録に実際の席を書く | 同じ失敗を毎ラウンド待たない。前の head からの差分を渡してよいかを、実際に読んだ席で判定する |
| 席の claude の起動を cross-refactoring と同じ関数（`read_only_launch`・`answer_text`）にする | 同じ役割の起動を 2 か所に書くと、許す Tool の変更が片方にしか効かない |
| 席の名前とランタイムの名前を分け、引くのは `runtime_of` の 1 か所 | 記録は席ごとに分け、監視と利用上限の検知はランタイムの値で正しく引く |
| 埋めた claude には `--base` からの全差分を渡し、ラウンドの範囲（記録の `scope`・詳細にレビューした節）は差し替えない | 初めて加わる席は前回までの差分を読んでいない。範囲を差し替えると、他の席が読んでいない節まで読んだことになる |
| 記録のコメントのラウンドの表に「相手役」の列（`_seats_cell`）を足す | どのラウンドを誰が読んだかを PR で読めるようにする。表を読み戻す処理は無い（`open_items` は最後の行の JSON だけを読む） |

## テスト観点

相手役の起動は偽物にし、起動ごとに回答の有無を決めて駆動を通す（`plugins/ndf/tests/core/converge/test_converge.py`・
`plugins/ndf/tests/domains/crossreview/test_crossreview_seats.py`・`test_crossreview_run.py`・`test_crossreview_scope.py`・
`test_crossreview_report.py`・`plugins/ndf/tests/adapters/test_cli_floor.py`・`test_claude_args.py`）。

- 既定の席で kiro だけが起動し直しても結果を返さないと、同じラウンドで claude の席が 1 回だけ起動され、codex と claude の指摘で判定し、注意・`reviewers`・`filled`・「相手役」の列に埋めたことが載ること
- 以後のラウンドで kiro を起動せず、codex には前の head からの差分、埋めた claude には全差分が渡ること
- `allowed` が claude と codex なら席が `codex`・`claude`、claude だけなら `claude`・`claude-2` になり、`run` と `review-scope` が同じ席で範囲を決めること
- `claude-2` の起動・無進捗の許容がランタイム claude で引かれ、出力のファイルが `round<n>-claude-2` になること
- 両方が結果を返せば claude を起動せず `filled` が無いこと。全員が結果を返さなければ `precondition`、claude の席が結果を返さなければ埋めずに `incomplete_reviewers` で止まり、claude の席を起動し直さないこと
- 名指し 1 者（重なりを除いた数）では埋めず、2 者の名指しは既定と同じに埋めること
- 始める前の確かめで断られた claude 以外の席を `unready: ` の理由で埋め、修正担当か名指し 1 者が断られたら `precondition` で止まり、同じランタイムの確かめは 1 回で、再利用の判定より後に行うこと
- 同じ head の打ち直しで、前回の席に今回の席を 1 対 1 で割り当てられるときだけ前回の結果を返し、`warnings` に埋めたことが載ること
- 結果の有無が、完了かつ `findings` 配列の有無と、claude の席の差分の読み取りの拒否で決まること
- 席の claude の起動の引数に `--restricted`・`--add-dir <review-input-*>`・`--permission-mode dontAsk` があり `acceptEdits` が無いこと。資料のディレクトリが前の実行のディレクトリやリンクを使わず、終わりに消えること
- `--restricted` を含む起動は 2.1.315 か版を確かめられないと子のプロセスを起動せずに断られ、含まない起動（修正担当・cross-refactoring）は今の下限のままであること
- cross-refactoring の `assign` と読むだけの起動の引数・本文の取り出しが変わらないこと

## 関連リンク

- [issue #3955](https://github.com/devbasex/ai-plugins/issues/3955) — 空いた席を claude の読むだけの起動で埋める（11 系）
- [10 系の参加者プールとスロット](cross-review-participants-and-seats-10x.md) — v10.27.77 までの仕組みの記録（この文書から分けた）
- [ラウンドで担当が受け取るもの](cross-review-round-inputs.md) — 出し切りの指示・スナップショットの取り直し・設計 Pull Request の観点（#542 #786）
- [`cross-review` の状態とレビューの手順](../../plugins/ndf/skills/cross-review/docs/01-state-and-review.md)
- [`cross-review` の手順](../../plugins/ndf/skills/cross-review/SKILL.md)
- [証拠ベースのレビューと効果の測定](cross-review-evidence-based.md) — 指摘の構造化・実行検証・反証と、状態ファイルのそれ以外の鍵
- [起動結果と上限の検知](cross-review-launch-outcome.md) — 結果なしの理由の語彙とリトライ可否
