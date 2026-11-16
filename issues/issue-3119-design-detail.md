# #3119 の詳細設計: ai-plugins のブランチ運用の設定と運用の文書の書き換え

設計文書 [issue-3119-design.md](issue-3119-design.md) の「構成要素」を、ファイルとセクションの粒度へ下ろした文書である。

**この課題はコードを足さず、変えもしない。** 書き換えるのは設定（`.ndf/branch-strategy.json` の作成と `.ndf/worktree.json`）と運用の文書だけである。
設定のパース・`main` 宛の検査・全体テストの省略の判定は、NDF の既存の関数（#3121・#3163）がそのまま受ける。
そのため関数を単位にするセクション（クラスと関数の構造・関数の仕様・異常系の処理・処理の手順・ログ・再利用・単体テスト）は
`省く:` にし、代わりに「ファイル配置」の下にファイルとセクションごとの変更点を置く。

## ファイル配置

触るファイル（`*` は新設）:

```text
.ndf/
├── branch-strategy.json              * # 設定。セクション branch_strategy を作る
└── worktree.json                     # 設定。セクションへ移したキーを外す
AGENTS.md                             # Git運用ルール・版とリリースの方針・DO の 1 行
CONTRIBUTING.md                       # 開発の進め方・一括の整形の取り込み・Pull Request を出す
docs/
├── versioning-and-distribution.md    # 正本の文書。チャネルと ref・正式版を出す
├── plugin-development-guide.md       # 既存プラグインの削除の手順
├── specifications/
│   └── ndf-branch-flow-declaration.md  # 末尾の 1 文を削除
├── glossary/
│   └── glossary.json                 # 用語「戻すマージ」（設計 PR で反映済み）
└── glossary.md                       # glossary.py render の出力（同上）
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `.ndf/branch-strategy.json` | 新設 | ai-plugins のブランチの流れ 4 項目・レビューの単位 4 行・マージの方式・検証環境・バージョンの保存先の値の正本を持つ | ndf-next のセクションのスキーマ（`branch-strategy.schema.json`）とブランチ戦略の一覧 |
| `.ndf/worktree.json` | 変更 | セクションへ移した `base_branch`・`production_branch`・`branch_flow` を外し、worktree の作り方の設定（`guard`）だけを持つ | ndf-next の `worktree.schema.json` |
| `docs/versioning-and-distribution.md` | 変更 | 各ブランチの役割、選んで集める手順、戻すマージ、依存するスプリント、本番の不具合の直し、リリース方式を戻す順序、レビューの単位の表を持つ | `.ndf/branch-strategy.json`（値の意味を説明する）、用語集 |
| `AGENTS.md` | 変更 | 分岐元とマージ先、チャネルの役割、リリース方式とレビューの単位の要点を持ち、手順を正本の文書へリンクで渡す | `docs/versioning-and-distribution.md` |
| `CONTRIBUTING.md` | 変更 | NDF のスプリントの外の変更の分岐元とマージ先（決定 4）を外部の貢献者へ示す | `AGENTS.md` |
| `docs/plugin-development-guide.md` | 変更 | 既存プラグインの削除の手順のマージ先を決定 4 に合わせる | `AGENTS.md` |
| `docs/specifications/ndf-branch-flow-declaration.md` | 変更 | NDF の設定の仕様から ai-plugins の値の記述を除く（決定 5） | 無し |
| `docs/glossary/glossary.json` | 変更 | 用語「戻すマージ」の定義を持つ。設計 PR の時点で追加済みのため、実装 PR では触らない | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` が `glossary.json` から作る。実装 PR では触らない | `docs/glossary/glossary.json` |

**依存の向きは一方向で、循環が無い。** 設定は NDF のスキーマだけに依存し、文書の側を読まない。正本の文書は設定と用語集を指す。
`AGENTS.md` は正本の文書を指し、`CONTRIBUTING.md` と `docs/plugin-development-guide.md` は `AGENTS.md` を指す。
確定仕様（`ndf-branch-flow-declaration.md`）は ai-plugins のどのファイルも指さない（設計文書の「コンテキスト」の公表された言語の関係）。

### `.ndf/branch-strategy.json` の作成と `.ndf/worktree.json` の書き換え

ブランチ戦略は `.ndf/branch-strategy.json` のセクション `branch_strategy` に書く（#3572 の決定 27）。`.ndf/worktree.json` の `base_branch`・`production_branch`・`branch_flow` をセクションへ移し、
`base_branch` を `main` に、ブランチの流れを GitHub flow の行の値にする。`.ndf/worktree.json` には `version`・`guard` が残る。移すのは #3613 の移行のコマンドで、
書き換えた後の 2 つのファイルの全体は「内部のデータと定数」にある。
リリース方式は GitHub flow（`release_strategy: trunk`。設計文書の決定 21）で、この値は今の NDF（`plugins/ndf/`）のブランチ戦略の一覧に無い。
書き換えは ndf-next への入れ替え（11.0.0）と同じ時点で行う。行は #3572 が ndf-next のブランチ戦略の一覧へ足す。
メインディレクトリの未コミットの同じファイル（`whole` の値）は、この worktree の書き換えに使わない（決定 6。扱いは「並行と排他」）。

### `docs/versioning-and-distribution.md` のセクションごとの変更

| セクション | いまの記述 | 書き換えた後に持つこと |
| --- | --- | --- |
| チャネルと ref（冒頭の段落） | 開発の変更は `develop` へマージし、正式版のときだけ `develop` の承認したコミットを `main` へ送る | スプリントは `main` から切り、承認したスプリントだけをリリースブランチへ集めて `main` へ送る。`develop` は開発版のデプロイとスプリントどうしの結合を試す場で、`main` へはマージされない |
| チャネルと ref（チャネルの表） | 正式版・開発版の 2 行 | 2 行のまま。開発版の行の「何が載るか」を「スプリントの検査を通ったスプリントがそのまま載る」にする。スプリントブランチ・リリースブランチの役割は表の後の段落で書く（チャネルではないため表に足さない） |
| 正式版を出す（冒頭の段落） | `develop` から `release/v<版>` を切る。head は `develop` か `origin/develop` の祖先の `release/v<版>` | `main` から `release/v<版>` を切り、選んだスプリントブランチをマージのコミットで集める。`main` 宛の検査の 3 条件は確定仕様の「ai-plugins の本番チャネル宛の検査」へリンクし、条件を写さない |
| 正式版を出す（承認の後に `develop` が進んでも〜） | `develop` の HEAD を比べる相手に入れない | 主語をスプリントブランチへ替える。承認の後にスプリントブランチへ入った変更は、このバージョンに入らず次のリリースで出る |
| 正式版を出す（`release/v<版>` を `develop` へ取り込む〜） | バージョン番号の衝突は本番のバージョンを取る | 用語「戻すマージ」を見出しの太字に使い、手順は変えない（決定 17 の手順と同じ） |
| 正式版を出す（この手順では `main` に〜） | `main` は `develop` の fast-forward から外れる | 段落を残し、比べる相手を「出した `release/v<版>`」だけにする |
| 正式版を出す（Pull Request のベースは `develop`） | `--base develop` を必ず付ける | マージ先は PR の種類で決まる。設計・実装・確定仕様化の PR は `sprint/<名前>`、スプリントの検査の PR は `develop`、リリースの PR は `main`。`gh pr create` には `--base` を必ず付ける |
| 正式版を出す（重いチェックを省く 2 条件） | 2 つ目は `develop` への push で通ったコミットからの差分 | 2 つ目の条件はマージ先の設定が `selected` のとき当たらないことを 1 文で足す（確定仕様の規則 3 へリンク） |
| 正式版を出す（新しいサブセクション 4 つ） | 無し | 「依存するスプリントを出す」「本番の不具合を直す」「リリース方式を戻す」（決定 20 の 4 つのステップの順序）「PR の種類ごとのレビュー」（決定 1 の表から `release` の行を除いた 4 行の表。値は設定と同じ） |

新しいサブセクションは `###` で「正式版を出す」の下に置く。この文書は 439 行あり（2026-11-13 に `wc -l` で数えた）、4 つのサブセクションで 80 行前後増えると 500 行を超える見込みである。
超えた分は、「正式版を出す」を別の文書へ分けずに縮める。置き換える段落（`develop` を本流とする説明・`--base develop` の段落）を
新しい段落へ書き換え、足し引きで縮める。通しで読む手順のため、`document-style` の分割の判断基準で単一ファイルに残す。
縮めても 500 行を超えるときは、`document-style` の行数の基準どおりセクションごとに分ける。

### `AGENTS.md` のセクションごとの変更

| セクション | いまの記述 | 書き換えた後に持つこと |
| --- | --- | --- |
| Git運用ルール（1 行目） | Pull Request のマージ先は `develop` | スプリントは `main` から切り、PR のマージ先は種類で決まる（スプリントブランチ・`develop`・`main`）。表は正本の文書にあるとリンクする |
| Git運用ルール（`--base develop` の付け忘れ〜） | head は `develop` か `origin/develop` の祖先の `release/v<版>` | `main` 宛は、選んだスプリントブランチを集めたリリースブランチだけを通す（`scripts/check-pr-base.sh` が `main` の設定で判定する）。条件の詳細は正本の文書へ |
| 版とリリースの方針（チャネルの表の前後） | 開発版はマージされた変更がそのまま載る | 開発版の行の文言を正本の文書と同じにする。リリース方式（承認したスプリントだけを `main` へ）とレビューの単位の要点を 1 段落ずつ足し、値の正本は `.ndf/branch-strategy.json` のセクション `branch_strategy` だと書く |
| ベストプラクティスの DO | `develop` 宛の PR を通じてマージ | スプリントブランチ宛の PR を通じてマージ |

`AGENTS.md` には表と手順を足さない（決定 3・I4）。書き換えた後に `instructions-check.py --root .` が 0 を返すことを確かめる。

### `CONTRIBUTING.md` のセクションごとの変更

| セクション | いまの記述 | 書き換えた後に持つこと |
| --- | --- | --- |
| 開発の進め方（マージ先の箇条書き） | マージ先は `develop`、`--base develop` を付ける | マージ先は保守者が課題に示すスプリントブランチ（`sprint/<名前>`）。`gh pr create --base sprint/<名前>` |
| 開発の進め方（worktree の用意） | `origin/develop` から切る | `origin/main` から切る |
| 一括の整形の前から続くブランチを取り込む | `origin/develop` の `.git-blame-ignore-revs` を読み、`origin/develop` をマージする | 読む先とマージする先を `origin/main` にする。`main` の `.git-blame-ignore-revs` にも同じ 2 行のコミットがある（`git show origin/main:.git-blame-ignore-revs \| grep -cE '^[0-9a-f]{40}$'` が 2。2026-11-13 に実行） |
| Pull Request を出す（2.） | `--base develop` を付ける | `--base sprint/<名前>` を付ける。名前は課題に示されたもの |

一括の整形のセクションは I5 の検索（`origin/develop` を読む記述）に掛かるため、
同じ実装 PR で直す。

### `docs/plugin-development-guide.md` の変更

「既存プラグインの削除」の手順 3 のマージ先と例を `develop` から `sprint/<名前>` へ、例のブランチの分岐元を `origin/main` へ替える。
「通常の取得経路で確かめたい場合は、開発版のチャネル（`develop`）へ出す」の段落は、開発版の取得の説明であってマージ先の規則ではないため残す。

### `docs/specifications/ndf-branch-flow-declaration.md` の変更

「ai-plugins の本番チャネル宛の検査」のセクションの末尾（251 行目）にある次の 1 文を削除する。
「ai-plugins の `.ndf/worktree.json` は `branch_flow` を持たず、既定値の行で動いている。」代わりの文は書かない（決定 5）。同じセクションのほかの段落と、`measure` の閾値の行（292 行目）は ai-plugins の値を書いていないため残す。

## クラスと関数の構造

省く: 新設・変更するクラスと関数が無い。設定を読む `branch_flow.parse_flow`・`branch_flow.read_flow` と、検査の `scripts/check-pr-base.sh`・`scripts/ci-heavy-skip.py` は #3121・#3163 のまま変えない

## 関数の仕様

省く: シグネチャ・責務・失敗の扱いのいずれかを変える関数が無い（「クラスと関数の構造」と同じ理由）

## 異常系の処理

省く: 関数を足さず変えないため、関数の粒度の失敗が無い。設定の値が許す組み合わせから外れたときは ndf-next の設定のスキーマの検査と、#3572 の詳細設計の `declaration.flow_of`・`declaration.review_problems` が断り、設定を読む工程か main 宛ての PR のチェックが止まる（設計文書の I1・I2）。ai-plugins の値（`main` の 2 つのブランチ・4 種類の行）は実装 PR の確認の手順で見る。運用の順序の失敗（メインディレクトリの追従の衝突・この課題のスプリントのマージからそのリリースのマージまでの間に始めたスプリント）は「並行と排他」に置く

## 処理の手順

省く: 分岐を持つ関数を足さない。切り替えのステップ 3〜5 の順序は設計文書の「処理の流れ」、PR の種類ごとのマージ先は「`docs/versioning-and-distribution.md` のセクションごとの変更」の表にある

## 内部のデータと定数

設定の値の唯一の保存先は `.ndf/branch-strategy.json` のセクション `branch_strategy` である。文書は値を写さず、正本の文書のレビューの単位の表だけが説明として値を並べる（決定 3・I3）。
書き換えた後の 2 つのファイルの全体は次のとおりである。`.ndf/branch-strategy.json` は #3572 の設計の「ブランチ戦略のセクション」の ai-plugins の例と同じで、スキーマの案に通ることは
#3572 の確かめ 8 にある。

```json
{
  "version": 1,
  "branch_strategy": {
    "base_branch": "main",
    "production_branch": "main",
    "release_strategy": "trunk",
    "start": "production",
    "integrate": "release",
    "doc_prs": "sprint",
    "reviews": {
      "impl": {"checks": ["cross-review"], "diff": "pr"},
      "sprint": {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"},
      "design": {"checks": ["cross-review"], "diff": "pr"},
      "spec": {"checks": [], "diff": "none"}
    },
    "production_merge": {"method": "merge-queue"},
    "verify": {"kind": "marketplace", "ref": "verify/next", "marketplace": "ai-plugins-next"},
    "release": {"version_store": {"kind": "plugin"}}
  }
}
```

`.ndf/worktree.json`:

```json
{
  "version": 1,
  "guard": {
    "allow_paths": ["issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".agents/", ".serena/", ".ndf/", ".gitignore"]
  }
}
```

`guard.allow_paths` は値を変えない。実装ではいまの複数行の書き方のまま残す。レビューの単位の `release` の行は、GitHub flow の行がリリースの単位を選ばない（`release_units: false`）ため書けず、持たない。

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `branch_strategy.start` | 設定値 | `.ndf/branch-strategy.json` | `production`（スプリントを `main` から切る） |
| `branch_strategy.integrate` | 設定値 | `.ndf/branch-strategy.json` | `release`（スプリントの PR の `main` へのマージがリリースになる。マージの前は検証ブランチで試す） |
| `branch_strategy.doc_prs` | 設定値 | `.ndf/branch-strategy.json` | `sprint`（設計 PR と確定仕様化の PR のマージ先はスプリントブランチ） |
| `branch_strategy.release_strategy` | 設定値 | `.ndf/branch-strategy.json` | `trunk`（スプリントの PR を `main` へ直接マージする。develop と常設のリリースブランチを持たない） |
| `branch_strategy.reviews` | 設定値 | `.ndf/branch-strategy.json` | 4 種類の行（`impl`・`sprint`・`design`・`spec`。決定 1・決定 2） |
| `branch_strategy.base_branch` | 設定値 | `.ndf/branch-strategy.json` | `main`（本番ブランチと同じ。develop を持たない） |
| `branch_strategy.production_branch` | 設定値 | `.ndf/branch-strategy.json` | `main`（変えない） |
| `branch_strategy.production_merge`・`verify`・`release.version_store` | 設定値 | `.ndf/branch-strategy.json` | `merge-queue`・検証マーケットプレイス・`plugin`（#3572 の決定 17・21・22） |

キーの名前と許す値の正本は ndf-next のセクションのスキーマ（`contracts/schemas/declarations/branch-strategy.schema.json`）で、ai-plugins の側で値の一覧を持たない。
`trunk` の行が許す値（`start`・`doc_prs`・`integrate`）はセクションのスキーマへ、振る舞いの列（`keeps_sprint: false`・`release_from: production`・`release_units: false`）は
ndf-next のブランチ戦略の一覧（`contracts/domains/branch-flows.json`）へ #3572 が足す（#3572 の決定 28）。行の値は #3572 の設計の決定 5 にある。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| メインディレクトリの `develop` の追従と、要求定義の工程で書いた未コミットの設定 | メインディレクトリの `.ndf/worktree.json` | スプリントの検査の PR が `develop` へ入った後、追従の前に未コミットの差分を戻す（`git restore .ndf/worktree.json`。決定 6）。`.ndf/project.json` の未コミットの差分は触らない | 戻す値は要求の前提 4 に記録があり、新しい値はコミットに入っているため、戻しても失われる情報は無い。追従が衝突で止まったら、戻してから追従をやり直す |
| ステップ 3 のマージからステップ 4 のマージまでの間に始まるスプリント | `main` の設定（まだ `branch_flow` が無い）と、新しいスプリントの目録のスナップショット | 新しいスプリントを始めない（I7）。ステップ 4 のリリースのリクエストの前に、計画の状態で開始したスプリントが無いことを確かめる | 始めてしまったスプリントは止め、ステップ 4 の後に `main` から切り直す。切り直しは同じ課題で何度でも行える |
| この課題の実装 PR と、ほかのレーンが同じ文書（`AGENTS.md`・正本の文書）を変える PR | `AGENTS.md`・`docs/versioning-and-distribution.md` | 排他は取らない。2026-11-13 の時点で計画 p6 に動いているスプリントは lsw だけで、同時に変える PR は無い（要求の前提 3） | 後から入った側が衝突をふつうの衝突として解く |
| ステップ 4 のリリースの PR の検査と、ステップ 3 で `develop` に入った新しい設定 | `main` 宛の検査が読む設定 | 検査はマージ先（`main`）の設定だけを読み、`develop` と worktree の設定を読まない（#3121 の決定 12・I6） | ステップ 4 は今の規則（head が `develop` の祖先の `release/v<版>`）で通る。検査をやり直しても同じ結果になる |

## ログと記録の出力

省く: ログや記録を出す関数を足さず変えない。設定のパースと検査の結果の出力は #3121・#3163 のまま

## 既存の部品の再利用

省く: 新設する関数が無い。設定のパース（`branch_flow.parse_flow`）・マージ先の検査（`scripts/check-pr-base.sh`）・全体テストの省略（`scripts/ci-heavy-skip.py`）・レビューのずれの測定（`project-decl.py drift`）は既存のコンポーネントをそのまま使う（設計文書の「構成要素」の図）

## 単体テストの観点

省く: テストを足さない（要求の「前提とする取り決め」）。実装 PR で確かめるコマンドと壊し方は設計文書の「テスト設計」にある。設定の値は `branch_flow.read_flow` で指摘が出ないことで見る。`AGENTS.md` は `instructions-check.py --root .` が 0 を返すことで見る
