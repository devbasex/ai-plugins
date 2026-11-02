# Factory のオーガナイザーの設計: NDF の側の切り替え口と語の改名（#2439）

設計文書 [issue-2439-design.md](issue-2439-design.md) から分けたファイルである。`plugins/ndf/` に置く切り替え口と、語の改名（F9）を扱う。どちらも Factory を名指ししない（`plugins/` は `factory/` を参照しない。`scripts/check-service-boundary.py`）。

## 切り替え口: 外のオーガナイザーとファイルでやり取りする

### 具体例

Factory のループ `l20261102` の 3 周目と 4 周目の実行ジョブで、次の順に進む。

1. ランナーは `NDF_ORGANIZER_DIR=<状態の置き場>/organizer`・`NDF_ORGANIZER_LANE=loop-l20261102` を渡す
2. NDF は本番のステージを組む時点で、外のオーガナイザーのレーンと判定する。`requests/loop-l20261102-r3f9a1c2e4b7d.json` を書いて周回を配布待ちで締める
3. ランナーはそれを `release.requested` へ写す
4. 4 周目のジョブの開始で、ランナーは `answers/loop-l20261102-r3f9a1c2e4b7d.json`（`released`・`10.28.0`）を書く
5. NDF は周回の境でそれを読んで残りの工程を流す

### 置き場の契約（`plugins/ndf/contracts/forms/parallel-external.json`。新規）

```text
<NDF_ORGANIZER_DIR>/
├── requests/<依頼の id>.json      # NDF が書く（レーン）
├── answers/<依頼の id>.json       # 外のオーガナイザーが書く
├── ranges/<連番>.json             # 外のオーガナイザーが書く（配布済みの範囲）
├── batch.json                     # 外のオーガナイザーが書く（配布の実行だけ）
├── presented/<連番>-<提示>.json   # NDF が書く（配布の実行）
├── progress/<連番>.json           # NDF が書く（配布の実行）
└── results/<連番>.json            # NDF が書く（配布の実行）
```

| ファイル | 形 |
| --- | --- |
| `requests/` | #2436 の `parallel-request.json` の依頼（`kind: release`）。`{kind, id, lane, prs, issues, sprint_state, at}` に `ndf_version` を足す |
| `answers/`・`ranges/` | #2436 の `parallel-request.json` の答え `{result, version, release, reason, at}` と範囲 `{sha, version, prs, lanes}` |
| `batch.json` | `{seq, step, lanes:[{lane, requests:[{id, prs}], issues}], presentations, choices}`。`step` は `present` / `represent` / `confirm:1` / `confirm:2` / `continue` |
| `presented/` | `{seq, k, sha, version, prs, lanes:[{lane, requested}], added_prs, auto}` |
| `progress/` | `{seq, stages}`。届いた段（`release_branch` / `develop_merged` / `prod_pr_opened` / `main_merged` / `tagged`） |
| `results/` | `{seq, result, version, prod_pr_url, sha, prs, lanes, reason}`。`result` は `released` / `failed` |

- **どちらの書き手も一時ファイルへ書いて名前を付け替える。** 読み手は名前の付いたファイルだけを読む。書きかけのファイルを読まないためである
- 契約の例の組（依頼・答え・範囲・`batch.json`・提示・結果の 1 組ずつ）を置く。構造チェック（`scripts/contracts_lib/model.py`）とランナーのテストが同じ例を読む（`ndf-next-block.json` と同じ扱い）
- 環境変数の宣言 `plugins/ndf/contracts/env/NDF_ORGANIZER_DIR.json`・`NDF_ORGANIZER_LANE.json` を足す。`NDF_FACTORY_RUN` の宣言は #2436 が足す

### レーンの側

| 点 | 決まり |
| --- | --- |
| 判定 | `parallel_lane.lane_of()` は、`NDF_ORGANIZER_DIR` があれば外のオーガナイザーのレーン（名前は `NDF_ORGANIZER_LANE`）を返す。無ければ #2436 の判定のまま |
| 依頼 | `parallel.py request --external` は `requests/<id>.json` を書いて 10 で終わる。自分の Pull Request がすべて `origin/main` にあれば、`ranges/` から版を読んで書かずに 0 で終わる（#2436 の I15）。依頼の `id` の決め方は #2436 のまま |
| 答え | `parallel.py await --external --timeout 0` は `answers/<id>.json` を読む。無ければ 3 |
| 問い | 外のオーガナイザーのレーンは、設計の承認と承認ゲートの外の問いを依頼にしない（#2436 の I17 を当てない）。今どおり止まって人へ戻す。問いの hook は `NDF_ORGANIZER_DIR` があれば送らない |
| ループ | 周回の結果「配布待ち」と周回の境の読み方は #2436 のレーンのループのまま。答えを運ぶのは外のオーガナイザー（ランナー）である |

### 配布の実行の側

`/goal /ndf:development-workflow --parallel-helper --external` で始める。conductor は `batch.json` の `step` ごとに次の 1 手を打つ。

| `step` | すること | 終わり方 |
| --- | --- | --- |
| `present` / `represent` | `parallel.py compose --external` が提示の番号 `k`（`present` は 1、`represent` は 2）の提示のプランと本番のプランを `<置き場>/releases/<連番>/<k>/` に作る（#2436 の「配布を組む」と同じ関数。版は `origin/develop` の `plugin.json` の開発版から接尾辞を外す）。`supervise.py queue present.json --then release.json` を流す | MVV 判定が通せば本番まで流して `settle`。承認ゲート 2 で止まれば `parallel.py present --external` が `presented/` を書き、「判断が要る」で終える |
| `confirm:<k>` | 確定した提示 `k` の本番のプランだけを流す（#2436 の確定の後と同じ） | `settle` |
| `continue` | 止まったプランを今の再開の手順で続ける | `settle` か、判断で止まる |

`parallel.py settle --external <連番> --done <D>` は、本番のプランの done とリリース記録を読む。届いた段を `progress/` に、結果を `results/` に書く。配布の実行の失敗の扱い（CI の失敗・版数の衝突の直し）は #2436 の一時レーンと同じで、直せなければ問いとして止まる。

`<置き場>` は、ランナーが S3 へ保存する NDF の状態の中に置く。配布の実行の再開（`confirm:k`・`continue`）は、保存した提示のプランと本番のプランから続ける。

### 構成要素と触るファイル

| ファイル | 変えること |
| --- | --- |
| `plugins/ndf/scripts/lib/parallel_external.py` | 置き場の読み書き（一時ファイルと付け替え）・`batch.json` の読み・`compose`・`present`・`settle` の外の版 |
| `plugins/ndf/scripts/lib/parallel_lane.py` | `lane_of()` の外のオーガナイザーの判定、`request`・`await` の外の版 |
| `plugins/ndf/scripts/parallel.py` | 副命令に `--external` を足す（`request`・`await`・`compose`・`present`・`settle`） |
| 問いの hook（`plugins/ndf/scripts/hook_lib/ask_guard.py`） | `NDF_ORGANIZER_DIR` があれば依頼を送らない |
| `plugins/ndf/contracts/forms/parallel-external.json`・`plugins/ndf/contracts/env/NDF_ORGANIZER_{DIR,LANE}.json` | 置き場の契約と例の組、環境変数の宣言 |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | `--parallel-helper --external` の手順の節 |
| `plugins/ndf/scripts/tests/test_parallel_external.py` | 下のテスト設計 |

### テスト設計

| 条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 判定 | `NDF_ORGANIZER_DIR` があると本番のステージが配布の依頼のプランになり、無いと #2436 の判定のまま | 環境変数を読まないと落ちる |
| 依頼と答え | 依頼のファイルが契約の例と同じ形で書かれ、答えのファイルを `await --timeout 0` が読む。打ち直しても同じ `id` | 形を変える・`id` を採番し直すと落ちる |
| 問い | 外のレーンの設計の承認と問いは依頼にならず、今どおり止まる | 問いの hook が送ると落ちる |
| 提示と確定 | `represent` で提示 2 のプランを作り、`confirm:1` は提示 1 の本番のプランだけを流す | 確定しない提示の本番のプランを流すと落ちる |
| 結果 | `settle` が `main` のマージの後に止まった本番のプランで `main_merged` を `progress/` に書く | 段を書かないと落ちる |

## 語の改名（F9）

### 用語

| 用語 | 改める前 | 対象 |
| --- | --- | --- |
| 手元のマシン | `手元のランナー` | 用語集の `ndf-factory`・Factory の確定仕様・`factory/` の説明文・`ja.json` |
| 手元のマシンの枠 | `手元のランナーの枠` | 同じ |
| 料金プラン | `ndf-factory` の文脈の裸の「プラン」 | 同じ。`ndf-workflow` の「プラン」（`plan.json`）と、Factory の文書の中の NDF のプラン（本番のプランなど）は変えない |

### 対象と件数（2026-11-02 の検索）

| 対象 | 改める前の語の件数 |
| --- | --- |
| Factory の確定仕様 `docs/specifications/ndf-factory-*.md` | `手元のランナー`38 件（11 ファイル中 6 ファイル）。「プラン」57 件（7 ファイル。NDF のプランを指す行を除いて改める） |
| `factory/README.md` | 「プラン」2 件 |
| `factory/web/src/i18n/ja.json` | `screens.runners``手元のランナー`・`mode.local``local（手元のランナー）`・`account.plan`「プラン: {{plan}}」・`repositories.maxConcurrent`「同時の実行の数（空ならプランの値）」の 4 件 |
| `factory/` のコメントとテストの期待値 | `手元のランナー`16 ファイル・「プラン」18 ファイル（`ja.json` を含む）。文言を照合するテスト（`App.test.tsx` など）は期待値を改めた文言へ合わせる |
| 用語集 | `手元のランナーの枠`の語、「同時のジョブの数」「実行の形態」などの意味の中の語、`ndf-factory` の「プラン」 |

件数は実装の時点で同じ検索をし直して照らす。対象の外は、発表資料（`docs/presentations/`）・記事（`docs/articles/`）・#2436 の設計文書・`issues/` の過去の文書である。

### 守ること

- コードの識別子（`LocalRunner`・`runnerId`・`Plan`・`plan`）・API の項目名・データの項目名・i18n のキーは変えない（AC 16）
- 用語集は、新しい語に `pending_source`（この設計文書）を置き、`plan-to-spec` が確定仕様を書いたときに `source` へ移す。`手元のランナー`は廃止した語として残し、`glossary.py check` が新しく足した行で拾う
- `ndf-factory` の「プラン」は廃止した語にしない。同じ語が `ndf-workflow` で生きており、用語チェックは生きた語と同じ廃止した語を照合から外すためである。Factory の文書に裸の「プラン」が残らないことは、実装の検索で確かめる
