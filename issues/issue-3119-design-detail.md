# #3119 の詳細設計: ai-plugins のブランチ運用の宣言と運用の文書の書き換え

設計文書 [issue-3119-design.md](issue-3119-design.md) の「構成要素」を、ファイルと節の粒度へ下ろした文書である。

**この課題はコードを足さず、変えもしない。** 書き換えるのは宣言（`.ndf/worktree.json`）と運用の文書だけで、
宣言の読み分け・`main` 宛の検査・全体テストの省略の判定は NDF の既存の関数（#3121・#3163）がそのまま受ける。
そのため関数を単位にする節（クラスと関数の構造・関数の仕様・異常系の処理・処理の手順・ログ・再利用・単体テスト）は
`省く:` にし、代わりに「ファイル配置」の下にファイルと節ごとの変更点を置く。

## ファイル配置

触るファイル（`*` は新設。この課題に新設は無い）:

```text
.ndf/
└── worktree.json                     # 宣言。branch_flow を足す
AGENTS.md                             # Git運用ルール・版とリリースの方針・DO の 1 行
CONTRIBUTING.md                       # 開発の進め方・一括の整形の取り込み・Pull Request を出す
docs/
├── versioning-and-distribution.md    # 正本の文書。チャネルと ref・正式版を出す
├── plugin-development-guide.md       # 既存プラグインの削除の手順
├── specifications/
│   └── ndf-branch-flow-declaration.md  # 末尾の 1 文を削除
├── glossary/
│   └── glossary.json                 # 用語「戻すマージ」（設計 Pull Request で反映済み）
└── glossary.md                       # glossary.py render の出力（同上）
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `.ndf/worktree.json` | 変更 | ai-plugins のブランチの流れ 4 項目とレビューの単位 5 行の値の正本を持つ | NDF の `lib/branch_flow.py` のスキーマと許す値の表 |
| `docs/versioning-and-distribution.md` | 変更 | 各ブランチの役割、選んで集める手順、戻すマージ、依存するスプリント、本番の不具合の直し、取り込み方を戻す順序、レビューの単位の表を持つ | `.ndf/worktree.json`（値の意味を説明する）、用語集 |
| `AGENTS.md` | 変更 | 起点と宛先、チャネルの役割、取り込み方とレビューの単位の要点を持ち、手順を正本の文書へリンクで渡す | `docs/versioning-and-distribution.md` |
| `CONTRIBUTING.md` | 変更 | NDF のスプリントの外の変更の起点と宛先（決定 4）を外部の貢献者へ示す | `AGENTS.md` |
| `docs/plugin-development-guide.md` | 変更 | 既存プラグインの削除の手順の宛先を決定 4 に合わせる | `AGENTS.md` |
| `docs/specifications/ndf-branch-flow-declaration.md` | 変更 | NDF の宣言の仕様から ai-plugins の値の記述を除く（決定 5） | 無し |
| `docs/glossary/glossary.json` | 変更 | 用語「戻すマージ」の定義を持つ。設計 Pull Request の時点で追加済みのため、実装 Pull Request では触らない | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` が `glossary.json` から作る。実装 Pull Request では触らない | `docs/glossary/glossary.json` |

**依存の向きは一方向で、循環が無い。** 宣言は NDF のスキーマだけに依存し、文書の側を読まない。正本の文書は宣言と用語集を指し、
`AGENTS.md` は正本の文書を、`CONTRIBUTING.md` と `docs/plugin-development-guide.md` は `AGENTS.md` を指す。
確定仕様（`ndf-branch-flow-declaration.md`）は ai-plugins のどのファイルも指さない（設計文書の「コンテキスト」の公表された言語の関係）。

### `.ndf/worktree.json` の書き換え

`base_branch`・`production_branch`・`guard` は変えず、`branch_flow` を足す。足した後の全体は「内部のデータと定数」にある。
メインディレクトリの未コミットの同じファイル（`whole` の値）は、この worktree の書き換えに使わない（決定 6。扱いは「並行と排他」）。

### `docs/versioning-and-distribution.md` の節ごとの変更

| 節 | いまの記述 | 書き換えた後に持つこと |
| --- | --- | --- |
| チャネルと ref（冒頭の段落） | 開発の変更は `develop` へマージし、正式版のときだけ `develop` の承認したコミットを `main` へ送る | スプリントは `main` から切り、承認したスプリントだけをリリースブランチへ集めて `main` へ送る。`develop` は開発版のデプロイとスプリントどうしの結合を試す場で、`main` へは流れない |
| チャネルと ref（チャネルの表） | 正式版・開発版の 2 行 | 2 行のまま。開発版の行の「何が載るか」を「スプリントの検査を通ったスプリントがそのまま載る」にする。スプリントブランチ・リリースブランチの役割は表の後の段落で書く（チャネルではないため表に足さない） |
| 正式版を出す（冒頭の段落） | `develop` から `release/v<版>` を切る。head は `develop` か `origin/develop` の祖先の `release/v<版>` | `main` から `release/v<版>` を切り、選んだスプリントブランチをマージのコミットで集める。`main` 宛の検査の 3 条件は確定仕様の「ai-plugins の本番チャネル宛の検査」へリンクし、条件を写さない |
| 正式版を出す（承認の後に `develop` が進んでも〜） | `develop` の先端を比べる相手に入れない | 主語をスプリントブランチへ替える。承認の後にスプリントブランチへ入った変更は、この版に入らず次のリリースで出る |
| 正式版を出す（`release/v<版>` を `develop` へ取り込む〜） | 版数の衝突は本番の版を取る | 用語「戻すマージ」を見出しの太字に使い、手順は変えない（決定 17 の手順と同じ） |
| 正式版を出す（この手順では `main` に〜） | `main` は `develop` の fast-forward から外れる | 段落を残し、比べる相手を「出した `release/v<版>`」だけにする |
| 正式版を出す（Pull Request のベースは `develop`） | `--base develop` を必ず付ける | 宛先は PR の種類で決まる。設計・実装・確定仕様化の PR は `sprint/<名前>`、スプリントの検査の PR は `develop`、リリースの PR は `main`。`gh pr create` には `--base` を必ず付ける |
| 正式版を出す（重いチェックを省く 2 条件） | 2 つ目は `develop` への push で通ったコミットからの差分 | 2 つ目の条件は宛先の宣言が `selected` のとき当たらないことを 1 文で足す（確定仕様の規則 3 へリンク） |
| 正式版を出す（新しい小節 4 つ） | 無し | 「依存するスプリントを出す」「本番の不具合を直す」「取り込み方を戻す」（決定 20 の 4 段の順序）「PR の種類ごとのレビュー」（決定 1 の 5 行の表。値は宣言と同じ） |

新しい小節は `###` で「正式版を出す」の下に置く。この文書は 439 行あり（2026-11-13 に `wc -l` で数えた）、4 つの小節で 80 行前後増えると 500 行を超える見込みである。
超えた分は、「正式版を出す」を別の文書へ分けずに、置き換える段落（`develop` を本流とする説明・`--base develop` の段落）を
新しい段落へ書き換えて足し引きで縮める（通しで読む手順のため、`document-style` の分割の判断基準で単一ファイルに残す）。
縮めても 500 行を超えるときは、`document-style` の行数の基準どおり節ごとに分ける。

### `AGENTS.md` の節ごとの変更

| 節 | いまの記述 | 書き換えた後に持つこと |
| --- | --- | --- |
| Git運用ルール（1 行目） | Pull Request の宛先は `develop` | スプリントは `main` から切り、PR の宛先は種類で決まる（スプリントブランチ・`develop`・`main`）。表は正本の文書にあるとリンクする |
| Git運用ルール（`--base develop` の付け忘れ〜） | head は `develop` か `origin/develop` の祖先の `release/v<版>` | `main` 宛は、選んだスプリントブランチを集めたリリースブランチだけを通す（`scripts/check-pr-base.sh` が `main` の宣言で判定する）。条件の詳細は正本の文書へ |
| 版とリリースの方針（チャネルの表の前後） | 開発版はマージされた変更がそのまま載る | 開発版の行の文言を正本の文書と同じにする。取り込み方（承認したスプリントだけを `main` へ）とレビューの単位の要点を 1 段落ずつ足し、値の正本は `.ndf/worktree.json` の `branch_flow` だと書く |
| ベストプラクティスの DO | `develop` 宛の PR を通じてマージ | スプリントブランチ宛の PR を通じてマージ |

`AGENTS.md` には表と手順を足さない（決定 3・I4）。書き換えた後に `instructions-check.py --root .` が 0 を返すことを確かめる。

### `CONTRIBUTING.md` の節ごとの変更

| 節 | いまの記述 | 書き換えた後に持つこと |
| --- | --- | --- |
| 開発の進め方（宛先の箇条書き） | 宛先は `develop`、`--base develop` を付ける | 宛先は保守者が課題に示すスプリントブランチ（`sprint/<名前>`）。`gh pr create --base sprint/<名前>` |
| 開発の進め方（worktree の用意） | `origin/develop` から切る | `origin/main` から切る |
| 一括の整形の前から続くブランチを取り込む | `origin/develop` の `.git-blame-ignore-revs` を読み、`origin/develop` を取り込む | 読む先と取り込む先を `origin/main` にする。`main` の `.git-blame-ignore-revs` にも同じ 2 行のコミットがある（`git show origin/main:.git-blame-ignore-revs \| grep -cE '^[0-9a-f]{40}$'` が 2。2026-11-13 に実行） |
| Pull Request を出す（2.） | `--base develop` を付ける | `--base sprint/<名前>` を付ける。名前は課題に示されたもの |

一括の整形の節は I5 の検索（`origin/develop` を読む記述）に掛かるため、
同じ実装 Pull Request で直す。

### `docs/plugin-development-guide.md` の変更

「既存プラグインの削除」の手順 3 の宛先と例を `develop` から `sprint/<名前>` へ、例のブランチの起点を `origin/main` へ替える。
「通常の取得経路で確かめたい場合は、開発版のチャネル（`develop`）へ出す」の段落は、開発版の取得の説明であって宛先の規則ではないため残す。

### `docs/specifications/ndf-branch-flow-declaration.md` の変更

「ai-plugins の本番チャネル宛の検査」の節の末尾の 1 文「ai-plugins の `.ndf/worktree.json` は `branch_flow` を持たず、既定値の行で動いている。」
（251 行目）を削除する。代わりの文は書かない（決定 5）。同じ節のほかの段落と、`measure` の閾値の行（292 行目）は ai-plugins の値を書いていないため残す。

## クラスと関数の構造

省く: 新設・変更するクラスと関数が無い。宣言を読む `branch_flow.parse_flow`・`branch_flow.read_flow` と、検査の `scripts/check-pr-base.sh`・`scripts/ci-heavy-skip.py` は #3121・#3163 のまま変えない

## 関数の仕様

省く: シグネチャ・責務・失敗の扱いのいずれかを変える関数が無い（「クラスと関数の構造」と同じ理由）

## 異常系の処理

省く: 関数を足さず変えないため、関数の粒度の失敗が無い。宣言の値が許す組み合わせから外れたときは既存の `parse_flow` が指摘を返し、宣言を読む工程が止まる（設計文書の I1・I2）。運用の順序の失敗（メインディレクトリの取り込みの衝突・段 3〜段 4 の間に始めたスプリント）は「並行と排他」に置く

## 処理の手順

省く: 分岐を持つ関数を足さない。切り替えの段 3〜5 の順序は設計文書の「処理の流れ」、PR の種類ごとの宛先は「`docs/versioning-and-distribution.md` の節ごとの変更」の表にある

## 内部のデータと定数

宣言の値の唯一の置き場は `.ndf/worktree.json` である。文書は値を写さず、正本の文書のレビューの単位の表だけが説明として値を並べる（決定 3・I3）。
書き換えた後のファイルの全体は次のとおりである。

```json
{
  "version": 1,
  "base_branch": "develop",
  "production_branch": "main",
  "branch_flow": {
    "start": "production",
    "integrate": "check",
    "doc_prs": "sprint",
    "promotion": "selected",
    "reviews": {
      "impl": {"checks": ["cross-review"], "diff": "pr"},
      "sprint": {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"},
      "release": {"checks": ["cross-review"], "diff": "resolution"},
      "design": {"checks": ["cross-review"], "diff": "pr"},
      "spec": {"checks": [], "diff": "none"}
    }
  },
  "guard": {
    "allow_paths": ["issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".agents/", ".serena/", ".ndf/", ".gitignore"]
  }
}
```

`guard.allow_paths` は値を変えない。実装ではいまの複数行の書き方のまま残し、差分を `branch_flow` の追加だけにする。

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `branch_flow.start` | 設定値 | `.ndf/worktree.json` | `production`（スプリントを `main` から切る） |
| `branch_flow.integrate` | 設定値 | `.ndf/worktree.json` | `check`（スプリントの検査の後に `develop` へ入れる） |
| `branch_flow.doc_prs` | 設定値 | `.ndf/worktree.json` | `sprint`（設計 PR と確定仕様化の PR の宛先はスプリントブランチ） |
| `branch_flow.promotion` | 設定値 | `.ndf/worktree.json` | `selected`（選んだスプリントだけをリリースブランチへ集める） |
| `branch_flow.reviews` | 設定値 | `.ndf/worktree.json` | 5 種類の行（決定 1・決定 2） |
| `base_branch` | 設定値 | `.ndf/worktree.json` | `develop`（変えない。開発版のチャネルと結合の場） |
| `production_branch` | 設定値 | `.ndf/worktree.json` | `main`（変えない） |

キーの名前と許す値の正本は NDF の `lib/branch_flow.py`（取り込み方の表と `REVIEW_ALLOWED`）で、ai-plugins の側で値の一覧を持たない。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| メインディレクトリの `develop` の追従と、要求の段で書き起こした未コミットの宣言 | メインディレクトリの `.ndf/worktree.json` | スプリントの検査の PR が `develop` へ入った後、追従の前に未コミットの差分を戻す（`git restore .ndf/worktree.json`。決定 6）。`.ndf/project.json` の未コミットの差分は触らない | 戻す値は要求の前提 4 に記録があり、新しい値はコミットに入っているため、戻しても失われる情報は無い。追従が衝突で止まったら、戻してから追従を打ち直す |
| 段 3 のマージから段 4 のマージまでの間に始まるスプリント | `main` の宣言（まだ `branch_flow` が無い）と、新しいスプリントの目録のスナップショット | 新しいスプリントを始めない（I7）。段 4 のリリースの依頼の前に、計画の状態で開始したスプリントが無いことを確かめる | 始めてしまったスプリントは止め、段 4 の後に `main` から切り直す。切り直しは同じ課題で何度でも行える |
| この課題の実装 PR と、ほかのレーンが同じ文書（`AGENTS.md`・正本の文書）を変える PR | `AGENTS.md`・`docs/versioning-and-distribution.md` | 排他は取らない。2026-11-13 の時点で計画 p6 に動いているスプリントは lsw だけで、同時に変える PR は無い（要求の前提 3） | 後から入った側が衝突をふつうの衝突として解く |
| 段 4 のリリースの PR の検査と、段 3 で `develop` に入った新しい宣言 | `main` 宛の検査が読む宣言 | 検査は宛先（`main`）の宣言だけを読み、`develop` と worktree の宣言を読まない（#3121 の決定 12・I6） | 段 4 は今の規則（head が `develop` の祖先の `release/v<版>`）で通る。検査をやり直しても同じ結果になる |

## ログと記録の出力

省く: ログや記録を出す関数を足さず変えない。宣言の読み分けと検査の結果の出力は #3121・#3163 のまま

## 既存の部品の再利用

省く: 新設する関数が無い。宣言の読み分け（`branch_flow.parse_flow`）・宛先の検査（`scripts/check-pr-base.sh`）・全体テストの省略（`scripts/ci-heavy-skip.py`）・レビューのずれの測定（`project-decl.py drift`）は既存の部品をそのまま使う（設計文書の「構成要素」の図）

## 単体テストの観点

省く: テストを足さない（要求の「前提とする取り決め」）。実装 Pull Request で確かめるコマンドと壊し方は設計文書の「テスト設計」にあり、宣言の値は `branch_flow.read_flow` で指摘が出ないこと、`AGENTS.md` は `instructions-check.py --root .` が 0 を返すことで見る
