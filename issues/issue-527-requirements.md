# #527: `development-workflow` の起動時に作業ツリーの宣言を確かめる

設計は [issue-527-design.md](issue-527-design.md) にある。この文書は「何を満たすか」だけを扱う。

モードは `standard` である。配布する Skill の手順と、`worktree-setup.sh` の呼び出される約束
（副コマンド）を変える。

## 目的

`development-workflow` を起動した会話で、作業ツリー運用が宣言の無いまま黙って無効になる
状態を無くす。**宣言の無いリポジトリで何もしない `worktree` の性質は保つ。**

## 対象範囲

含む:

- `development-workflow` の本文の先頭に、宣言を確かめる手順を置く
- 宣言の状態（あり / なし / 読めない / 判定できない）を終了コードで返す読み取り専用の副コマンド
- 判定結果の出力へ、宣言の状態を載せる
- `worktree` の本文から、その副コマンドへ辿れるようにする

含まない:

- `worktree-guard.sh` / `worktree-session.sh` / `hooks/*.json` の変更（宣言の無いリポジトリで
  何もしない性質を変えない）
- `workflow-guard.sh` と `development-workflow` の frontmatter の変更（#565 の対象）
- `worktree-setup.sh init` が読めない宣言を「既にあります」と報告する件（#573）
- `base_branch` / `production_branch` を利用者に代わって推測して書くこと
- 盤面の工程の値と工程表の行の追加
- **クラス図**: 型を持つ言語の変更を含まないため対象が無い
- **ER 図・テーブル定義・CRUD 図・画面**: 永続データと画面を触らないため対象が無い
- **API 仕様記述（OpenAPI）**: 変わる約束はシェルの副コマンドであり、形式は設計文書の表で書く

## 依頼（原文）

> **`development-workflow` は `.ndf/worktree.json` の有無を一度も見ない。** 宣言が無い
> リポジトリで起動しても工程はそのまま進み、作業ツリー運用だけが黙って無効になる。

> **宣言が無いリポジトリで何もしないのは、`worktree` の設計である。** 他のリポジトリを
> 巻き込まないための性質であり、これは変えない。変えるのは **`development-workflow` を
> 起動したという意思表示があるとき**の扱いだけである。

> - `development-workflow` の判定の手順へ、宣言の有無を確かめる段を足す。無ければ `worktree` の
>   「0. 宣言ファイルを用意する」を通してから先へ進む
> - **置く位置が論点である。**（中略）**確認はモード判定より前が候補になる**
> - **拒否か案内かが論点である。**（中略）どちらを採るかと、その理由が要る
> - **案内だけで済ませると v10.5.0 の再現になりうる。**

受け入れ条件の原文は issue #527 の本文にある。「受け入れ条件」の節は、その 5 件を観測できる
形へ書き直したものである。

## 根拠の行番号（2026-09-13 の `develop` で数え直した値）

| 事実 | 根拠 |
| --- | --- |
| 宣言が無ければ `wt_declaration` は何も出さず 1 を返す | `plugins/ndf/scripts/lib/worktree-common.sh:143`（不在は 147 行、壊れた JSON と未対応の版も同じ 1） |
| 宣言が無ければ hook もコマンドも何も出さない | `plugins/ndf/skills/worktree/SKILL.md:63-64` |
| 宣言を作る手は `worktree` の手順 0 だけ | 同 53-61 行（`worktree-setup.sh init`） |
| `development-workflow` に宣言の確認が無い | `plugins/ndf/skills/development-workflow/SKILL.md` で `.ndf/worktree.json` が現れるのは 294 行（`production_branch` の読み取り）だけ。issue の 289 行は、その後の追記で 5 行ずれた |
| `worktree` を通らない経路がある | 同 118 行（`light` / `operation` の「主ディレクトリで編集してよいパスだけなら不要」） |
| `workflow-guard.sh` は宣言を読まない | `plugins/ndf/skills/development-workflow/scripts/workflow-guard.sh` の全 90 行に `worktree.json` が現れない |

### 設計の前に実測したこと

宣言の無い一時リポジトリ（`git init` 直後）で次を確かめた。

| 操作 | 結果 |
| --- | --- |
| `worktree-setup.sh status` | 「宣言ファイル: なし」を出し、終了コード 0 |
| `worktree-session.sh` へ `SessionStart` の入力を流す | 何も出さず、終了コード 0 |
| `worktree-guard.sh` へ `src/a.py` の `Write` の入力を流す | 何も出さず、終了コード 0 |
| 壊れた JSON を置いて `worktree-setup.sh status` | 「読めません」を出し、終了コード 0 |
| 壊れた JSON を置いて `worktree-setup.sh init` | **「宣言ファイルは既にあります」を出し、終了コード 0**（#573 として起票） |
| 宣言を作った後に `worktree-guard.sh` へ同じ入力を流す | 案内の JSON を出す |

**`status` は 3 つの状態を区別するが、終了コードでは区別しない。** `init` は読めない宣言を
「ある」と報告する。どちらも、手順が機械的に分岐する入口にならない。

## 前提

- 前提 1: 「起動した」とは、`development-workflow` の本文が会話へ読み込まれたことを指す。
  Skill ツール・スラッシュコマンド・Kiro の steering・agy の Skill のいずれで読み込まれても同じ
- 前提 2: 受け入れ条件 2 の「振る舞いが変わらない」は、**宣言ファイルを書き換えず、手順 1 以降の
  判定と工程表が従来どおり進む**ことを指す。確認のコマンドを 1 回実行し、判定結果の出力へ
  宣言の状態を 1 行足すことは変化に数えない
- 前提 3: `development-workflow` の frontmatter の `hooks` は、2026-09-13 の実測で発火していない
  可能性が高い（#565）。**この変更は hook の発火に依存しない形で受け入れ条件を満たす**
- 前提 4: 宣言を作る手は `worktree` の手順 0（`worktree-setup.sh init`）のまま 1 つに保つ

## 受け入れ条件

- [ ] 条件 1: 宣言の無いリポジトリで `worktree-setup.sh check` を実行すると、終了コード 2 を返し、ファイルを作らない
- [ ] 条件 2: 読めない宣言のあるリポジトリで `check` を実行すると終了コード 3、読める宣言では 0、git のリポジトリの外では 1 を返す
- [ ] 条件 3: `status` の「宣言ファイル:」の行が、`check` と同じ関数から状態を得ている（3 つの状態で出力が変更前と一致する）
- [ ] 条件 4: `development-workflow` の本文で、宣言の確認が「判定の手順」の 1 より前にある。終了コード 2 のときは `worktree` の手順 0 を通してから先へ進むと書かれている
- [ ] 条件 5: 宣言の無い一時リポジトリで、Claude Code に `development-workflow` を起動させる。手順 1 へ進む前に `.ndf/worktree.json` が作られる。判定結果の出力に宣言の状態の行が載る。実機で 1 回通し、会話の記録を残す
- [ ] 条件 6: 宣言のあるリポジトリで同じ起動をすると、`.ndf/worktree.json` の内容が変わらない。判定結果の出力に「あり」が載る。実機で 1 回通す
- [ ] 条件 7: 宣言の無いリポジトリで、`worktree-session.sh` と `worktree-guard.sh` へ hook の入力を流す。どちらも変更前と同じく、何も出さず終了コード 0 で終わる
- [ ] 条件 8: この変更の差分に、次の 5 つが含まれない
  - `hooks/*.json`
  - `worktree-guard.sh`
  - `worktree-session.sh`
  - `workflow-guard.sh`
  - `development-workflow` の frontmatter
- [ ] 条件 9: 本文に「拒否しない」ことと、その理由が書かれている。理由は、起動の時点に止める引き金が無いこと・4 ランタイムで同じに働かせること・#565 の 3 つである
- [ ] 条件 10: 宣言の状態を 3 つへ分ける処理が `worktree-common.sh` の 1 関数だけにあり、`check` と `status` はそれを呼ぶ。`development-workflow` の本文と `workflow-guard.sh` は、`.ndf/worktree.json` の存在を自分で調べない。`init` が上書きを避けるために見る存在の確認は、状態の分類ではないため対象外とする
- [ ] 条件 11: 読めない宣言のとき、本文の手順は `init --force` を実行せず、状態を示して止まる
- [ ] 条件 12: 判定できないとき、本文の手順は工程を止めず、判定結果の出力に「判定できない」と理由を載せる

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `check` は通信しない。origin への問い合わせ（`ls-remote`）を含む関数を呼ばない |
| 運用・保守性 | 宣言の状態が判定結果の出力に残り、呼び出し側（親のエージェント・利用者）が読める |
| システム環境 | 4 ランタイム（Claude Code / Codex / Kiro CLI / agy）で同じ本文の手順が働く。hook の対応の差に依存しない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `worktree-setup.sh` に `check` を足す。`init` / `status` の出力と終了コードは変えない |
| データ | 変わらない |
| 既存の振る舞い | `development-workflow` の起動時に確認のコマンドが 1 回走る。宣言が無ければ `init` が走る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest plugins/ndf/skills/worktree/tests plugins/ndf/skills/development-workflow/tests -q` |
| 静的解析 | `bash -n plugins/ndf/scripts/worktree-setup.sh plugins/ndf/scripts/lib/worktree-common.sh`（この環境に `shellcheck` は無い） / `python3 scripts/check-skill-frontmatter.py` / `bash scripts/validate-runtime-plugins.sh` |
| 手動確認 | 条件 5・6 は `claude -p --plugin-dir` を一時リポジトリで実行し、`.ndf/worktree.json` の有無・内容と出力の行を見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存のテストの実行、`status` の出力が変わらないことの確認 |
| 確認してから行う | hook の定義や `workflow-guard.sh` に触ること（#565 と重なる） |
| 行わない | 宣言の無いリポジトリで hook が何かを出すようにすること、関門を増やすこと |

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| 宣言 | 主ディレクトリの `.ndf/worktree.json` |
| 宣言の状態 | あり（読める）/ なし（ファイルが無い）/ 読めない（ファイルはあるが `wt_declaration` が 1 を返す）/ 判定できない（git・jq が無い、リポジトリの外、`$SCRIPTS` を決められない） |
| 起動していない会話 | `development-workflow` の本文が読み込まれていない会話 |
| 判定の実体 | 宣言の状態を決めるコード。本文と hook はこれを呼ぶだけで、基準を書き写さない |
