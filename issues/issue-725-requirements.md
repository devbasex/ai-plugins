# #725: 通過工程の控えを記録のスクリプト自身が積むようにし、控えの取りこぼしと食い違いを根本原因の場所で直す

正は課題の本文（#725）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> 同じ根本原因を持つ課題（子 issue）を、現れている場所ではなく根本原因の場所で直すための親 issue である（`backlog-refinement` の判定「ルートコーズ」）。再現手順と観測は各子 issue にある。
>
> ## 修正レイヤー
>
> **通過工程の控えを積む責務の置き場所。**
>
> - いまは PreToolUse の hook（`plugins/ndf/skills/development-workflow/scripts/workflow-guard.sh` → `lib/workflow-common.sh` の `wf_is_candidate` / `wf_parse_sync`。呼び出しは `workflow-guard.sh:47` / `:64`、定義は `workflow-common.sh:234` / `:289`）が、実行されるコマンドの文字列から「記録のスクリプトが呼ばれた」ことを推測して控えを積む（`references/stage-completeness.md`）
> - 記録を実際に行うスクリプト（`plugins/ndf/scripts/progress-record.sh` / `projects-sync.sh`）は、控えを積まない（`grep -n stage-check` → 0 件）
> - 推測なので、スクリプトの呼び方（並べる・番号を変数で書く・例として文字列に書く・片方だけを呼ぶ）と、hook が動く版（配布済みの版の工程名の一覧）によって、本文と控えが食い違う
>
> ## 修正方針
>
> 移動（`move_responsibility`）。記録のスクリプトが、盤面の宣言の有無を見る前（`projects-sync.sh:67` の `DECL=$(pj_declaration "$top_dir") || exit 0` より前）に `stage-check.sh record` を自分で呼ぶ。hook がコマンドの文字列から控えを積む形は外す。
>
> **#1142 の決定 20（スプリント 2b の移行ステップ D5）は、D5（f04abfad、PR #1294）で hook の語の分割を Python（`hook.py words`）へ置き換えた。** `workflow-guard.sh:64` の `wf_parse_sync` の呼び出しは D5 の後も残っている。この issue は D5 の後の作業として、残った `wf_parse_sync` の呼び出しを `workflow-guard.sh` から外し、記録のスクリプトが控えを積む形にする。
>
> マージの関門のように、外部のコマンドを観測するしかない判定は残る。その字句解析は #724 が持つ。
>
> ## 子 issue
>
> | 子 issue | 現象レイヤー | 観測 |
> | --- | --- | --- |
> | #452 | `progress-tracking` の呼び方（本文の記録と控えの 2 本） | 課題の本文と控えが食い違っても、どちらも気づかない |
> | #487 | 控えを積む hook（`wf_parse_sync`） | 1 回の実行につき 1 件しか積まれず、番号を変数で書くと 1 件も積まれない |
> | #580 | 控えを積む hook（`wf_parse_sync`） | ヒアドキュメントやコメントに書いた例が、実行していない工程として積まれる |
> | #459 | 控えを積む hook（配布済みの版の工程名の一覧） | 開発中の版で記録した工程名が、配布済みの版の hook に無く黙って捨てられる |
> | #961 | 控えを積む hook（`development-workflow` の Skill 単位の hook） | 3 層の supervisor は同 Skill を起動しないため hook が働かず、控えが 1 件も積まれない |
>
> ## 完了条件
>
> - 記録のスクリプトが自分で控えを積み、hook はコマンドの文字列から控えを積まない
> - 複数の記録を並べる・番号を変数で書く・例として文字列に書く・片方のスクリプトだけを呼ぶ、のどの形でも本文と控えが一致することを検査が確かめる
> - 盤面の宣言が無いリポジトリでも控えが積まれる
> - 各子 issue の再現手順を実行し、現象が出ないことを確かめる（子 issue はその時点の棚卸が「閉じてよい」で閉じる）
> - `progress-tracking` の「工程の単位と記録する課題」にある「後片付けまでの記録は課題ごとに 1 回の実行にする」の制約を外す（回避が要らなくなるため）
>
> ## 他の課題との関係
>
> - **#845 の「development-workflow との接点」2 項と、#856 の `sprint-close.py`・#857 の `merged-steps.py`（どちらも閉じた）が、「後片付けまでの記録は 1 実行に 1 件」の制約を前提にしている。** この issue は完了条件でその制約を外すため、入った後に接点 2 項と両スクリプトの前提を緩める
> - **#843 の設計は控えを正として読む。** `issues/issue-829-830-design.md` 235 行は `stage-check.sh report <番号>` を「本文と食い違えば控えを正とする」として読む。控えの取りこぼしが、#844 の新しい hook の判定にも波及する
> - コマンド単位の字句解析は #724 が持つ（#580 を共有する）
>
> ## 関連
>
> - #845 — Skill の主体をスクリプトへ移す親（「development-workflow との接点」2 項）
> - #856（closed） — 「スプリントを閉じる」をスクリプトにした（`sprint-close.py`）
> - #857（closed） — merged の後片付けをスクリプトにした（`merged-steps.py`）
> - #1142 — 決定 20（D5、f04abfad）で hook の語の分割を Python へ置き換えた
> - #843 / #844 — #829 / #830 の設計と実装（控えを正として読む）
> - #724 — 1 回の実行をコマンド単位で字句解析・列挙する層

## 目的

- 通過記録を積む責務を、コマンドの文字列を観測する hook から、進捗記録を実際に行うスクリプト（`plugins/ndf/scripts/projects-sync.sh` / `plugins/ndf/scripts/progress-record.sh`）へ移す
- 呼び方（並べる・番号を変数で書く・例として文字列に書く・片方だけを呼ぶ）、実行の形（3 層の supervisor・`supervise.py` の子プロセス・Claude Code 以外のランタイム）、hook が動く版のどれによっても、課題の本文の「進行」と通過記録が食い違わない状態にする
- 「進捗記録は 1 回の実行に 1 件」という回避の制約を手順書から外す

## 現状（2026-10-02、`design/issue-725` の a09129f9 で確かめた）

| 箇所 | 現状 |
| --- | --- |
| `plugins/ndf/skills/development-workflow/scripts/workflow-guard.sh:47` / `:64` | `wf_is_candidate` が `projects-sync\.sh` に当たると、`wf_parse_sync` が最初の 3 語を読み、`wf_record` で通過記録へ積む。`配布` のときは `wf_report` の欠落を additionalContext で案内する（`:77`〜`:81`） |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh:234` / `:302` | `wf_is_candidate` / `wf_parse_sync` の定義 |
| `plugins/ndf/skills/development-workflow/SKILL.md` の frontmatter | hook は Claude Code で `development-workflow` を起動した会話の単位だけに登録される |
| `plugins/ndf/scripts/projects-sync.sh` / `progress-record.sh` | `stage-check.sh` を呼ばない（`grep -n stage-check` が 0 件） |
| `plugins/ndf/scripts/supervise_lib/state.py:264` の `record_stage` | `projects-sync.sh` を `subprocess.run` で呼ぶ。Bash の tool 実行ではないため、hook はこの記録を一度も見ない |
| 工程名の一覧 | 本文側は `plugins/ndf/scripts/lib/projects-common.sh:35` の `PJ_STAGES`、通過記録側は `workflow-common.sh` の `WF_STAGE_MATRIX` の 1 列目。2 か所にある |
| `plugins/ndf/skills/progress-tracking/SKILL.md:57` / `:135`〜`:139` | 「進捗記録は 1 回の Bash 実行に 1 件」「後片付けまでの記録は課題ごとに 1 回の実行」の回避を定めている |

## 前提

- 前提 1: 通過記録のファイルの名前・JSON の形・置き場所（`references/stage-completeness.md` の「置き場所」「鍵は課題のままにする」）と、`stage-check.sh` の `record` / `report` の引数と出力は変えない
- 前提 2: 通過記録へ積むのは、引数のチェックを通った進捗記録の呼び出しごとに 1 回である。課題の本文の書き換えが失敗した（`gh` が無い・課題を取得できない）ときも積む。いまの hook が「進捗記録が発行されたこと」を積んでいた意味を保つためである。そのため「本文と通過記録の一致」は、本文を書けた呼び出しについて判定する
- 前提 3: `projects-sync.sh` が中で `progress-record.sh` を呼んでも、1 回の進捗記録で通過記録へ積む値は、キー（`stage` / `mode` / `pace`）ごとに 1 件である。どちらのスクリプトが積むかは設計で決める
- 前提 4: `worktree` / `plan` / `status` のキーは、今と同じく通過記録へ積まない
- 前提 5: `progress-record.sh --repo <所有者>/<リポジトリ>` で別のリポジトリの課題へ書くときは、通過記録の鍵を `--repo` のリポジトリにする。いま作業しているリポジトリの同じ番号の通過記録へ積まない
- 前提 6: 記録のスクリプトから `stage-check.sh` へは、同じプラグインの根の中の相対で辿る（`workflow-common.sh` がプラグインの根の `scripts/lib/` を 4 階層の相対で指す #293 の形の逆向き）。そのため記録のスクリプトと工程名の一覧は常に同じ版になる
- 前提 7: 利用者の手元に旧い版の hook が残る移行の期間は、旧い hook と新しいスクリプトの両方が同じ工程を積むことがある。通過記録は追記だけで、判定は工程の集合で行う（`stage-completeness.md` の「報告の読み方」）ため、二重に積まれても判定は変わらない。旧い通過記録のファイルの移行は行わない
- 前提 8: `配布` へ進んだ時点の欠落の案内は、hook の additionalContext から、進捗記録のスクリプトの標準出力へ移す。進捗記録のスクリプトの出力は呼んだ AI が読むため、案内の届き先は変わらない（`supervise.py` は出力を捨てるが、Pull Request の作成の時点の検査と `merged` の報告は通過記録を読む）
- 前提 9: 承認ラベルによるマージの拒否と、Pull Request の作成の時点の実行証跡の案内（`workflow-guard.sh:55`・`:88`）は hook に残す。外部のコマンドを観測するしかない判定だからである

## 対象範囲

含む:

- 記録のスクリプト（`projects-sync.sh` / `progress-record.sh`）が自分で通過記録へ積む
- `workflow-guard.sh` から進捗記録の観測（`wf_parse_sync` の呼び出し）を外し、`wf_is_candidate` が進捗記録のスクリプトに当たらないようにする。使われなくなった `wf_parse_sync` を消す
- `配布` へ進んだ時点の欠落の案内を、進捗記録のスクリプトの出力へ移す
- 本文側と通過記録側の工程名の一覧が一致することを検査で確かめる（1 か所へまとめるかは設計で決める）
- `progress-tracking` の SKILL.md と `references/excerpt.md`、`development-workflow` の `references/stage-completeness.md`（「通過記録」の節・「呼び方」の「`record` は hook が自動で呼ぶ」）、`projects-sync.sh` の冒頭のコメントを、新しい積み方に合わせて書き直す。「1 回の実行に 1 件」「後片付けまでの記録は課題ごとに 1 回の実行にする」の制約を外す
- 子 issue（#452 #487 #580 #459 #961）と、本文のコメントにある devbase#249 の再現手順で、現象が出ないことを確かめる

含まない:

- #845 の「development-workflow との接点」2 項と、`sprint-close.py`（#856）・`merged-steps.py`（#857）の前提を緩めること。この issue が入った後に別の課題で扱う
- マージの拒否の字句解析（#724。「やらないと判断した」で閉じている）と、`wf_merge_target` の変更
- Pull Request の作成の時点の実行証跡の検査の変更
- 既にある通過記録のファイルの書き換え・移行
- ボードへの記録の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 進捗記録のスクリプトが呼ばれた | AI・supervisor・`supervise.py` が `projects-sync.sh` か `progress-record.sh` を実行した | — | — |
| E2 | 引数をチェックした | E1 | 知らないキー・工程表に無い値・引数の不足は終了コード 2 で終わり、本文にも通過記録にも何も書かない | E1 |
| E3 | 課題の本文の「進行」を書き換えた | E2 を通った（`stage` / `mode` / `pace` / `worktree` / `plan`） | `gh` が無い・課題を取得できないときは書かずに続ける（終了コード 0） | E2 |
| E4 | 通過記録へ 1 件積んだ | E2 を通った（`stage` / `mode` / `pace`） | リポジトリを特定できない・排他を取れないときは積まずに標準エラーへ 1 行残し、終了コード 0 で続ける | E2。E3 の成否には依らない（前提 2） |
| E5 | 記録の無い必須の工程を案内した | E4 で積んだ値が `stage` の `配布` | 欠落が無ければ何も出さない | E4 |
| E6 | ボードのフィールドを更新した | E2 を通り、ボードの設定がある | 設定が無ければ何もしない。問い合わせの失敗は 1 行知らせて終了コード 0 | E4 の後（ボードの設定の有無で E4 を飛ばさない） |
| E7 | hook が Bash の実行を観測した | Claude Code の PreToolUse | — | 進捗記録の文字列からは何も積まない |
| E8 | 通過記録を報告した | `stage-check.sh report`・Pull Request の作成の時点の検査・`merged` | 記録が無ければその旨を 1 行出す | E4 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 進捗記録 | 工程に入った時点で 1 回打つ記録。課題の本文の「進行」も同じ 1 回で更新される |
| 通過記録 | 通過工程を課題ごとに残したファイル |
| 通過工程 | ある課題について、進捗記録が実際に書かれた工程の集合 |
| ボード | 進行を記録する GitHub Projects のプロジェクト 1 つ。設定が無ければ何も動かない |

## 受け入れ条件

置き場所を一時ディレクトリへ隔離した（`XDG_STATE_HOME` と `CLAUDE_PLUGIN_DATA` を指定し、`gh` を偽物に差し替えた）状態で、自動テストが確かめる。

- [ ] 1 回の Bash 実行に `projects-sync.sh <N> stage "<工程>"` を 4 行並べて実行すると、通過記録の `stages` に 4 つの工程がすべて入り、課題の本文の「進行」の 4 つの工程にもチェックが付く
- [ ] `for n in <N1> <N2>; do bash projects-sync.sh "$n" stage "設計"; done` を実行すると、`<N1>` と `<N2>` の両方の通過記録に `設計` が入る
- [ ] `progress-record.sh <N> "<工程>"` だけを呼んでも、通過記録に `<工程>` が入る
- [ ] `projects-sync.sh <N> stage "<工程>"` を 1 回呼んだとき、通過記録の `stages` に増える値は 1 件である（`progress-record.sh` を経由しても 2 件にならない）
- [ ] `projects-sync.sh <N> mode standard` / `projects-sync.sh <N> pace fast` を呼ぶと、通過記録の `mode` / `pace` がその値になる。`worktree` / `plan` / `status` のキーでは通過記録が変わらない
- [ ] ボードの設定（`.ndf/projects.json`）が無いリポジトリで `projects-sync.sh <N> stage "<工程>"` を呼んでも、通過記録に `<工程>` が入る
- [ ] `gh` が無い環境で `projects-sync.sh <N> stage "<工程>"` を呼んでも、通過記録に `<工程>` が入り、終了コードは 0 である
- [ ] `progress-record.sh <N> "<工程>" --repo <別の所有者>/<別のリポジトリ>` を呼ぶと、`<別の所有者>__<別のリポジトリ>__<N>.json` に積まれ、いまのリポジトリの `<N>` の通過記録は変わらない
- [ ] `workflow-guard.sh` に、`projects-sync.sh <N> stage "<工程>"` を実行するコマンド・ヒアドキュメントの本文に例として書いたコマンド・コメントに書いたコマンドのどれを PreToolUse として渡しても、通過記録のファイルは作られず変わらない
- [ ] `workflow-guard.sh` と `workflow-common.sh` に `wf_parse_sync` の定義と呼び出しが無い（`grep -n wf_parse_sync` が 0 件）
- [ ] 記録の無い必須の工程がある課題で `projects-sync.sh <N> stage "配布"` を呼ぶと、標準出力に `記録なし:` を含む案内が出る。欠落が無ければ案内は出ない
- [ ] 知らないキー・工程表に無い値・引数の不足では、いまと同じく終了コード 2 で終わり、通過記録は変わらない
- [ ] 通過記録への書き込みが失敗した（排他を取れない・origin の URL を取れない）ときも、`projects-sync.sh` / `progress-record.sh` の終了コードは 0 で、課題の本文の書き換えは行われる
- [ ] 本文側の工程名の一覧（`PJ_STAGES`）と通過記録側の工程名の一覧（`WF_STAGE_MATRIX` の 1 列目）が同じ並びであることを、自動テストが確かめる（1 か所にまとめた場合は、そのテストは不要になる）
- [ ] `supervise.py` の `record_stage` が課題ごとに呼んだ `projects-sync.sh` の記録が、通過記録に入る（子プロセスとして起動した記録も積まれる）
- [ ] 承認ラベルによる設計 Pull Request のマージの拒否と、Pull Request の作成の時点の実行証跡の案内は、既存のテスト（`plugins/ndf/skills/development-workflow/tests/test_workflow_guard.py` ほか）がそのまま通る
- [ ] 子 issue #452 #487 #580 #459 #961 と devbase#249 の再現手順を実行し、各現象が出ないことを Pull Request の本文に記録する
- [ ] `progress-tracking` と `stage-completeness.md` から「1 回の実行に 1 件」「後片付けまでの記録は課題ごとに 1 回の実行にする」の制約が消え、通過記録を積むのが記録のスクリプトであると書かれている（`python3 plugins/ndf/scripts/doc-lint.py` とリンクのチェックが通る）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 通過記録へ積めないことを理由に、進捗記録のスクリプトの終了コードを 0 以外にしない（呼び出し側の誤りの 2 だけを除く）。進行管理が理由で開発の工程を止めない |
| 性能・拡張性 | 排他を取れたときに、1 回の進捗記録へ足される時間は 1 秒未満である（ボードと `gh` を除いた手元の処理で実測し、Pull Request の本文へ載せる）。排他を待つ上限は既存の `NDF_STAGE_LOCK_TIMEOUT`（既定 5 秒）のまま変えない。hook は進捗記録のスクリプトで語の分割を行わなくなる |
| 移行性 | 通過記録のファイルの形（`version: 1`）を変えない。旧い版の hook が併存しても、通過工程の判定は変わらない（前提 7） |
| システム環境 | Claude Code・Codex・Kiro CLI・agy のどれで記録のスクリプトを呼んでも積まれる（hook の有無・Skill の起動の有無に依らない） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `projects-sync.sh` / `progress-record.sh` / `stage-check.sh` の引数と終了コードは変わらない。`projects-sync.sh <N> stage 配布` の標準出力に欠落の案内が加わることがある |
| データ | 通過記録の形は変わらない。積まれる件数は、取りこぼしが無くなる分だけ増える |
| 既存の振る舞い | hook は進捗記録を積まなくなり、`配布` の時点の additionalContext を出さなくなる。マージの拒否と実行証跡の案内は変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/skills/development-workflow/tests plugins/ndf/scripts/tests -q -n 4`（範囲）と、`.ndf/project.json` の `test` の全体 |
| 静的解析 | `shellcheck` の CI（`lint.yml`）、`python3 plugins/ndf/scripts/doc-lint.py`、`python3 scripts/check-skill-frontmatter.py` |
| 再現手順 | 子 issue と devbase#249 の手順を、置き場所を隔離して実行する（#580 の手順は本文にある） |
| 手動確認 | 次の版を導入した後に、3 層の実行で進めた課題について `stage-check.sh report <番号>` が工程を返すことを確かめる（リリース後テストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 安定版の経路（`AGENTS.md` の「安定版と実験版」）。記録のスクリプトは `plugins/ndf/scripts/`、通過記録の判定は `plugins/ndf/skills/development-workflow/scripts/` に置いたままにする |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md`、`AGENTS.md` の「ベストプラクティス」（呼ぶ外部コマンドと自分の入出力の形を書く前に実行して確かめる）。`.md` の文言を照合するテストは書かない |
| テスト戦略 | 記録のスクリプトを偽物の `gh` と隔離した置き場所で実際に走らせる結合テストで、受け入れ条件の呼び方の形を 1 つずつ通す。hook は PreToolUse の入力を渡すテストで「積まない」ことを確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 範囲のテストと全体テストの実行、shellcheck、doc-lint、子 issue の再現手順の実行 |
| 確認してから行う | 通過記録のファイルの形・`stage-check.sh` の引数の変更（前提 1 で変えないと決めた。要るなら人へ戻す） |
| 行わない | 既存の通過記録の書き換え、#845 / #856 / #857 の前提の変更、マージの拒否の字句解析の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 通過記録へ積むのを `projects-sync.sh` と `progress-record.sh` のどちらに置くか（前提 3 の 1 件の条件を満たす形） | `design` | 設計 Pull Request |
| 工程名の一覧を 1 か所にまとめるか、一致の検査で済ませるか（Value 6 の「同じ役割の定数を分けない」に照らす） | `design` | 設計 Pull Request |
| `配布` の欠落の案内を `stage-check.sh record` の出力として返すか、記録のスクリプトが `report` を呼ぶか | `design` | 設計 Pull Request |
