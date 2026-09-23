# #828 / #680: サブエージェントに Skill 本文を丸ごと読ませず、仕事を分ける器を比べて選ぶ — 設計

要求と受け入れ条件は [issue-828-680-requirements.md](issue-828-680-requirements.md) にある。この文書は「どう作るか」だけを扱う。
決定の理由と採らなかった案は [issue-828-680-design-decisions.md](issue-828-680-design-decisions.md) にある。

## 例: 設計の持ち場の supervisor が 1 工程を記録するまで

**いまの形。** conductor が設計の supervisor に「工程に入った時点で `progress-tracking` を呼ぶ」と
指示する。supervisor は `/ndf:progress-tracking` を起動し、本文（約 7,600 トークン）を文脈へ
載せる。使うのは次の 2 行だけである。

```bash
bash "$SCRIPTS/progress-record.sh" 828 "設計" --mode standard
bash "$SCRIPTS/projects-sync.sh" 828 stage "設計"
```

さらに `$SCRIPTS` を決めるために `scripts-lookup.md`（約 150 行）を読む。`development-workflow` の
本文（約 10,700 トークン）を読んでモードと工程表を確かめる supervisor もいる（#827 の実測で
28 回）。**本文は持ち場が終わるまで文脈に残り、以後の呼び出しのたびに読み直される。**

**変更後の形。** conductor が起動指示の「記録のコマンド」の項目に、絶対パスを解いた 1 行を
書いて渡す。

```text
記録のコマンド: bash /home/u/.claude/plugins/cache/ai-plugins/ndf/10.17.1/scripts/projects-sync.sh <課題番号> stage "<工程名>"
```

supervisor は工程に入るたびに、課題ごとに 1 回ずつこの 1 行を打つ。issue の本文の `## 進行` と
盤面の両方に残り、通過工程の控えにも積まれる。**`progress-tracking` も `development-workflow` も
起動しない。** モードと通す工程は起動指示の「モード」「持ち場」の項目が既に持っている。

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | 記録のコマンド 1 行（issue の本文と盤面と控えに同時に残る） | supervisor / 対話の会話 / 工程の Skill |
| F2 | supervisor の起動指示の雛形（`development-workflow` と `progress-tracking` を読ませない） | conductor |
| F3 | worker の起動指示の雛形（Skill を起動させず、手順を抜粋で渡す） | supervisor |
| F4 | 抜粋の形と置き場所（`progress-tracking` の抜粋を見本として 1 つ） | 起動指示を書く側、#855 の検査、#845 の子 |
| F5 | 仕事を分ける器の比較表 | conductor / supervisor（器を選ぶとき）、新しい Skill を書く人 |
| F6 | 小さな作業にサブエージェントを起こさない線引き | supervisor（worker を起こす前） |

## 構成要素

| 要素 | 変更 | 責務 |
| --- | --- | --- |
| `plugins/ndf/scripts/projects-sync.sh` | 変える | **記録のコマンドの入口。** `stage` / `mode` / `worktree` / `plan` のキーでは、盤面の宣言の有無にかかわらず先に `progress-record.sh` を呼んで issue の本文を更新し、その後で盤面を更新する（F1） |
| `plugins/ndf/scripts/progress-record.sh` | 変える | issue の本文の `## 進行` の更新。工程名に `-` を受け、チェックリストを変えずに見出し行（モード・作業ツリー・計画ファイル）だけを更新する（F1） |
| `development-workflow/references/agent-layers.md` | 変える | 起動指示の雛形（F2 / F3）。「委譲の線」から器の比較表と線引きを指す |
| `development-workflow/references/work-vessels.md` | 新設 | 器の比較表と選び方（F5）、小さな作業の線引き（F6） |
| `development-workflow/references/context-window.md` | 変える | 「委譲する対象と、しない対象」から `work-vessels.md` を指す（写さない） |
| `development-workflow/references/stage-completeness.md` | 変える | 用語「進行の記録」の説明に、同じ 1 回で issue の本文も更新されることを足す（控えの読み方は変えない） |
| `progress-tracking/SKILL.md` | 変える | 「呼び方」を記録のコマンド 1 行にする。抜粋を指す |
| `progress-tracking/references/excerpt.md` | 新設 | 抜粋の見本（F4） |
| `plugins/ndf/skills/AUTHORING.md` | 変える | 抜粋の形と置き場所の規約（F4）。#855 の検査が読む目印 |
| 工程の Skill の末尾の記録の文（20 ファイル） | 変える | 「`/ndf:progress-tracking` を呼ぶ」を記録のコマンド 1 行へ（AC7） |
| `development-workflow/SKILL.md` | 変える | conductor が起動指示へ記録のコマンドを書くこと（`$SCRIPTS` を解いてから渡す） |
| テスト（`test_projects_sync*` ほか） | 足す | F1 の振る舞い（AC6 / AC8） |

変えないもの: 通過工程の控えの読み方（`workflow-common.sh` の `projects-sync.sh` の照合）、
`progress-record.sh --repo`（他のリポジトリの課題。盤面を書かない経路はそのまま残る）、
`progress-tracking` の「まとまりを閉じる」の手順（終わりの工程で 1 度だけ本文を読む）。

**図は実行の時に読み書きされる関係だけを描く。** 文面だけを変える要素は描かない。当たるのは `context-window.md` / `stage-completeness.md` / `progress-tracking/SKILL.md` / `AUTHORING.md` / 工程の Skill の末尾の文 / `development-workflow/SKILL.md` / テストである。`agent-layers.md` は起動指示の矢印（C → S → W）の中身として現れる。

```mermaid
flowchart TB
  C[conductor] -- 起動指示<br/>モード・工程・記録のコマンド --> S[supervisor]
  S -- 起動指示<br/>作業・手順の抜粋 --> W[worker]
  S -- 1 行 --> PS[projects-sync.sh]
  PS --> PR[progress-record.sh]
  PR --> IB[(issue の本文<br/>## 進行)]
  PS --> BD[(盤面)]
  H[PreToolUse hook<br/>workflow-common.sh] -. コマンドを観測 .-> PS
  H --> ST[(通過工程の控え)]
  S -. 器を選ぶ .-> WV[work-vessels.md]
  EX[excerpt.md] -. 写す .-> C
  EX -. 写す .-> S
```

## 入出力の契約

### 記録のコマンド（F1）

```text
projects-sync.sh <課題番号> <キー> <値>
  キー: stage | mode | status | worktree | plan
```

| キー | issue の本文（`progress-record.sh`） | 盤面 | 控え（hook） |
| --- | --- | --- | --- |
| `stage` | `progress-record.sh <課題> "<値>"`（チェックを付ける） | 工程のフィールド | 工程を積む（今と同じ） |
| `mode` | `progress-record.sh <課題> - --mode <値>`（見出し行だけ） | モードのフィールド | モードを書く（今と同じ） |
| `worktree` | `progress-record.sh <課題> - --worktree <値>` | 作業ツリーのフィールド | 読まない（今と同じ） |
| `plan` | `progress-record.sh <課題> - --plan <値>` | 計画ファイルのフィールド | 読まない（今と同じ） |
| `status` | 書かない | Status（「まとまりを閉じる」だけが使う） | 読まない |

| 条件 | 終了コード | 出力 |
| --- | --- | --- |
| 記録できた | 0 | `#<課題> 進行 = <工程>`（issue の本文）と、盤面の行（今と同じ） |
| 盤面の宣言が無い | 0 | issue の本文の行だけ（**今は何も出さずに抜けていた**） |
| `gh` が無い・issue を取得できない・盤面が上限 | 0 | 理由の 1 行（今の「何もしない条件」と同じ） |
| 知らないキー・工程表に無い値・引数の不足 | 2 | ERROR の行。issue の本文も盤面も書かない |

**引数の検査を先に行う。** 値の誤りで 2 を返すときに、issue の本文だけが書かれた状態を作らない。
`progress-record.sh` を呼ぶのは検査を通った後である。

**`progress-record.sh` を直接呼ぶ経路は残す。** `--repo` で他のリポジトリの課題へ書くとき
（盤面へは書かない）と、`--note` を足すときに使う。呼び出しの形は変えない。

### supervisor の起動指示（F2）

`agent-layers.md` の「conductor → supervisor」の `prompt` に必ず入れる項目を 8 から 9 にする。

| 項目 | 中身 | 変更 |
| --- | --- | --- |
| 持ち場 | 持ち場の名前と、通す工程の一覧 | そのまま |
| 課題 / モード / 作業場所 / 前の持ち場の報告 / 承認 / 到達点 / 提示物の置き場所 | — | そのまま |
| **記録のコマンド** | `bash <絶対パス>/projects-sync.sh <課題番号> stage "<工程名>"` の 1 行。**conductor が `$SCRIPTS` を解いた絶対パスで書く** | 足す |

supervisor が守る規則の変更:

| # | いま | 変更後 |
| --- | --- | --- |
| 3 | 工程に入った時点で `progress-tracking` を呼ぶ。記録のコマンドは 1 回の Bash 実行に 1 件 | 工程に入った時点で**起動指示の「記録のコマンド」を課題ごとに 1 回打つ**。記録のコマンドは 1 回の Bash 実行に 1 件。**`progress-tracking` と `development-workflow` を起動しない**（モードと工程は起動指示が持つ。まとまりを閉じる手順だけは本文を読む） |
| 7 | 委譲してよい作業は worker へ出す。委譲しない 5 つは自分で行う | 同じ文に「**小さな作業は worker へ出さずにその場で行う**（`work-vessels.md` の線引き）」を足す |

**規則の数は 10 のまま変えない。** conductor の起動指示と、それを写した既存の手順
（`handoff` の起動指示など）が「10 個」を数えているためである。

### worker の起動指示（F3）

`agent-layers.md` の「supervisor → worker」の `prompt` の項目を 4 から 5 にする。

| 項目 | 中身 | 変更 |
| --- | --- | --- |
| 作業 / 入力 / 返す形 / 置き場所 | — | そのまま |
| **手順** | 作業に要る手順の抜粋（呼び出し 1 行・結果の読み方・判断の基準）。**抜粋の元の Skill の `references/excerpt.md` を写す。** 抜粋が無い Skill の手順が要るときは、supervisor が要る段落だけを写す | 足す |

worker が守る規則に 6 を足す。

> 6. **Skill を起動しない。`SKILL.md` を読まない。** 手順は起動指示の「手順」に従う。手順が
>    足りないときは `結果: 判断が要る` で返す。**起動指示が Skill の起動そのものを手順として
>    渡したときだけ、その 1 つを起動してよい**

**例外は、手順として Skill の起動を渡された場合の 1 つだけである。** 当たるのは今
`cross-review` の修正（Step 5 / 7.5 の `/ndf:fix`。`fix/SKILL.md` が「サブエージェント側では
この SKILL.md を読み込んで」と定める）だけで、`fix` の本文を縮める #859 がこの経路を抜粋へ
置き換える。worker が自分の判断で工程の Skill を起動すると、その Skill の進行の記録・確認の段・
関門の判断が worker の文脈で動く。worker が持たない責務（進行の記録・収束の判定）を持つことになる。
そのため、起動してよいかは worker ではなく起動指示を書く側が決める。

### 抜粋の形（F4）

抜粋は `plugins/ndf/skills/<Skill名>/references/excerpt.md` に置く。**1 つの Skill に 1 ファイル。**

```markdown
<!-- ndf-excerpt: progress-tracking -->
# progress-tracking の抜粋

## 呼び出し

bash "$SCRIPTS/projects-sync.sh" <課題番号> stage "<工程名>"

## 結果の読み方

| 出力・終了コード | 意味 | 次にすること |
| --- | --- | --- |

## 判断の基準

- （呼ぶ時点・呼ばない条件・本文を読むべき場面を箇条書きで）
```

| 規約 | 内容 | 理由 |
| --- | --- | --- |
| 目印 | 1 行目の `<!-- ndf-excerpt: <Skill名> -->` | #855 の検査が抜粋を見つけ、本文との一致を確かめる起点にする |
| 見出し | `## 呼び出し` / `## 結果の読み方` / `## 判断の基準` の 3 つだけ、この順 | #845 の接点 9 と同じ形にする |
| 分量 | 40 行以内 | 写す側の起動指示に載る量である。超えるなら本文を縮める側（#845 の子）の課題である |
| 本文から指す | `SKILL.md` に「呼ぶ側へ渡す抜粋は `references/excerpt.md`」の 1 行を置く | 本文を直す人の目に抜粋が入る（非機能の運用・保守性） |
| 置かないこと | 本文に無い規則 | 抜粋は写しであり、正本は本文である |

**この変更で作る抜粋は `progress-tracking` の 1 つだけである。** 他の Skill の抜粋は、#845 の子が
`SKILL.md` を縮めるときに作る（要求の前提 2）。

## 仕事を分ける器（F5 / F6）

`work-vessels.md` の中身の設計である。**置き場所は `development-workflow/references/` で、
`agent-layers.md` の「委譲の線」から指す**（#680 で規則の置き場所に決まった位置）。

### 比べる器

| 器 | 何か | #845 の段 |
| --- | --- | --- |
| その場 | いまの会話の文脈で行う | 段 3（その会話の LLM） |
| サブエージェント | ホストの Agent ツールで別の窓を起こし、報告を受け取る | 段 3 |
| CLI 実行 | codex / agy / kiro-cli / claude を別プロセスで起動し、結果ファイルを読む | 段 3（別の LLM） |
| 最小構成の `claude -p` | 道具と指示を絞った 1 回の判断（#841） | 段 2（分類の判断） |
| スクリプト | 決まった手順を bash / Python で実行する。長いものは背景で起動して完了通知を待つ | 段 1 |

### 比べる観点と表

| 観点 | その場 | サブエージェント | CLI 実行 | 最小構成の `claude -p` | スクリプト |
| --- | --- | --- | --- | --- | --- |
| 上の層の文脈 | 読んだ量がそのまま残る | 報告の量だけ残る | 結果ファイルを読んだ量だけ | 判断の出力だけ | 出力を読んだ量だけ |
| 固定費 | 無し | 起こすたびに払う（指示・規約・道具の定義） | 起こすたびに払う。親の課金には載らない | 小さい（道具と指示を絞る） | 無し |
| 確実性 | 手順を読んだ本人が行う | 報告と実物が食い違うことがある（#585）。「必須」の手順が置き換わる（#676） | CLI ごとに完了の判定と終了コードの癖が違う | 出力の形を検査で縛れる | 手順どおりに動く。書いた範囲しか扱えない |
| 止まりにくさ | 待ちで応答を終えると止まる | 待ちで応答を終えると止まる（#656）。本体が落ちると全員止まる | 本体と別プロセスで動く | 1 回で終わる | 実行環境の上限を背景の起動で越えられる |
| 独立性 | 無し | 同じモデル・別の窓 | 別のモデル・別の視点 | 同じモデル・判断 1 つ | — |
| 資源 | 本体のメモリ | 本体のメモリ。並行の本数に数える（`parallel-work.md` の下限 6） | 別プロセスのメモリ。外部の利用上限 | 別プロセス | 実行する処理による |
| 観測性 | 会話の記録に残る | 会話の記録に残る（`skill-stats --agents`） | 結果ファイルと実行の記録（`run_metrics.py`） | 出力を残せば読める | 出力と終了コード |
| 手順の従いやすさ | 読んだ本文に従う | 渡した指示に従う。Skill を読ませると本文の全体に従おうとする | 渡したプロンプトに従う | 1 つの問いに答える | 従う必要が無い |

**可搬性の列は置かない。** 4 ランタイムでの扱いは切り出した課題が決める（決定の記録の決定 7）。

### 仕事ごとの選び方

| 仕事 | 器 | 根拠（表の観点） |
| --- | --- | --- |
| 決まった手順（進行の記録・集計・検査の実行・待ち） | スクリプト | 固定費も判断も要らない。LLM を起こさない |
| 小さな読解・1 コマンドの実行（下の線引きに当たるもの） | その場 | 固定費が読む量を上回る |
| 読む量が大きく返す量が小さい読解（探索・全文の読解・ログの集計） | サブエージェント（worker の `調査` / `集計`） | 上の層の文脈に残るのが報告だけになる |
| 作業ツリーを書き換える修正 | サブエージェント（worker の `修正`） | `agent-layers.md` の「委譲の線」と同じ（写さない） |
| 別の視点のレビュー・提案 | CLI 実行 | 独立性 |
| 分類の判断 1 つ | 最小構成の `claude -p`（#841 の後） | #841 が入るまでは選ばず、その場で行う |
| 600 秒を超える待ち | スクリプトを背景で起動して完了通知を待つ | `waiting.md` と同じ（写さない） |

**supervisor のスクリプト駆動（#827 の方針）はこの表の上で「工程の進行をスクリプトへ、判断を
最小構成の `claude -p` へ」と位置づける。** 採否は #827 が決める。

### 小さな作業にサブエージェントを起こさない（F6）

**起こす前に 1 件ごとに判定する。** 次のどちらかに当たる作業は worker へ出さず、その場で行う。

| # | 条件 | 例 |
| --- | --- | --- |
| 1 | **読む量の見込みが、worker の固定費を下回る** | 1 つのファイルの数十行を読む、`gh issue view` を 1 回打つ、記録のコマンドを打つ |
| 2 | **返す量が読む量と変わらない**（結果を上の層がそのまま読む） | 1 つのコマンドの出力を全部読む必要がある確認、短い差分の確認 |

- **固定費の値は書かない。** `context-window.md` の「固定費の値を規約へ書かない」に従い、
  導入した先で `skill-stats --agents` が測った値を使う。測っていなければ「起こす会話の最初の
  1 回の読み込みの量」を目安にする
- **読む量が見込めないときは、その場で量だけを測ってから決める**（`wc -l` や件数の問い合わせ）
- **この判定は起こす前の見込みである。** 起こした後の測定で、持ち場の中の小さな worker を
  束ねるか決めるのは #773 の候補 2 である。線引きは #773 の測定の入力になるが、#773 の判定を
  置き換えない

## 処理の流れ

### 記録のコマンド

```mermaid
sequenceDiagram
  participant S as supervisor
  participant H as PreToolUse hook
  participant P as projects-sync.sh
  participant G as GitHub
  S->>H: Bash「projects-sync.sh 828 stage "設計"」
  H->>H: 控えへ「設計」を積む（今と同じ）
  H-->>S: 通す
  S->>P: 実行
  P->>P: 引数の検査（誤りなら 2 で終わる）
  P->>G: progress-record.sh 828 "設計" で issue の本文の ## 進行 を差し替える
  P->>P: 盤面の宣言を読む（無ければここで 0）
  P->>G: 盤面のフィールドを更新する
```

### 起動指示を組む

```mermaid
sequenceDiagram
  participant C as conductor
  participant S as supervisor
  participant W as worker
  C->>C: development-workflow を読む。モード判定。$SCRIPTS を解く
  C->>S: 持ち場・モード・記録のコマンド（絶対パス）
  S->>S: 工程の Skill を起動する（持ち場の仕事）
  S->>S: work-vessels.md の線引きで、その場か worker かを決める
  S->>W: 作業・入力・手順（excerpt.md を写す）・返す形・置き場所
  W-->>S: ## 作業の報告
```

## 非機能の実現方式

| 条件 | 実現方式 |
| --- | --- |
| 記録のコマンドが今の 2 コマンドより遅くならない | 盤面への問い合わせは今と同じ回数。issue の本文の取得と更新は今の `progress-record.sh` と同じ 2 回 |
| 抜粋が本文を直す人の目に入る | 抜粋を同じ Skill の `references/` に置き、`SKILL.md` から 1 行で指す |
| 記録の失敗で工程を止めない | `progress-record.sh` の終了コードが 0 以外でも `projects-sync.sh` は盤面の更新へ進み、今の終了コードの契約（誤りだけ 2）を守る |

## 決定の記録

[issue-828-680-design-decisions.md](issue-828-680-design-decisions.md) に 9 件ある。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| AC1 / AC3 / AC4 | `agent-layers.md` の差分のレビュー（cross-review）。雛形の項目の表と規則の文を読む |
| AC2 | 同上。加えて、実装の後の最初の `/goal` で supervisor が `progress-tracking` / `development-workflow` を起動していないことを `skill-stats --agents` で見る |
| AC5 / AC6 | `projects-sync.sh` のテストに足す: 宣言なしで `stage` を呼ぶと issue の本文だけが更新される / 宣言ありで両方が更新される / `mode` で見出し行だけが変わる / 知らない工程名で 2 を返し本文を書かない（`gh` は既存のテストと同じ偽物で置き換える） |
| AC7 | `grep -rn "この工程に入ったら.*progress-tracking" plugins/ndf/skills/*/SKILL.md` が 0 件で、`design/SKILL.md` の「進行を記録する」の節も記録のコマンドの形になっている（今は 19 件の定型文と `design` の 2 件、計 20 Skill・21 件） |
| AC8 | 既存の `test_stage_check.py` / `test_workflow_guard.py` が変更なしで通る |
| AC9 | `progress-tracking/references/excerpt.md` の 1 行目の目印と見出し 3 つ。検査の実装は #855 |
| AC10 / AC11 | `work-vessels.md` の差分のレビュー。`agent-layers.md` と `context-window.md` から指されていること（`grep -n "work-vessels.md"`） |
| AC12 | 配布の後、`release-verification` で #827 の計測を同じ条件で回す（比べる前の値は下の表） |
| AC13 | 決定の記録の決定 7 と、起票した課題の番号 |

AC12 の比べる前の値（#827、2026-09-20 以降、5 リポジトリ、サブエージェント 156 件）:

| 値 | 変更前 |
| --- | ---: |
| Skill を読み込んだサブエージェント | 102 / 156 件 |
| サブエージェント内の Skill の読み込み | 371 回・約 230 万トークン |
| 持ち越し量に占める Skill 本文 | 16% |
| `progress-tracking` の読み込み | 40 回（1 回 7,649） |
| `development-workflow` の読み込み | 28 回（1 回 10,679） |

**減ると見込むのは `progress-tracking` と `development-workflow` の 2 行である。** 他の 4 つ
（`markdown-writing` / `cross-review` / `fix` / `pr`）は、この変更では減らない（要求の前提 1）。
supervisor が起動する工程の Skill と、その下で呼ばれる Skill だからである。#845 の子が縮める。`fix` の
33 回は `cross-review` の修正の worker が起動する経路で、worker の規則 6 の例外に当たる（#859）。

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| worker が Skill を起動しない規則の守られ方 | 規則は文面で縛るだけで、hook は置かない。守られたかは AC12 の計測で見る |
| 抜粋の分量の上限（40 行） | `progress-tracking` の 1 件でしか確かめていない。#845 の子が作るときに合わなければ #855 で直す |
| 対話の会話での記録 | 工程の Skill の末尾の文を 1 行にしたことで、対話の会話も `progress-tracking` を読まなくなる。`$SCRIPTS` の決め方は `scripts-lookup.md` を読む必要が残る（#847 が 1 コマンドにする） |
