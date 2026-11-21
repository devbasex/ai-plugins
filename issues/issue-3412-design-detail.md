# #3412 の詳細設計: 確定仕様化の手順と、前後の形を同じコマンドで数える計測

設計文書 [issue-3412-design.md](./issue-3412-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。確定仕様化の部分は関数を持たないので、手順とファイルの中身を書く。

## 地図

触るファイル（`*` は新設。削除する設計文書 36 本は [設計文書の「移す先の対応表」](./issue-3412-design.md#移す先の対応表) にある）:

```text
docs/specifications/
  ndf-rebuild.md *                 目的・構造・入れ替え・記号の表・番号の対応表
  ndf-rebuild-libraries.md *
  ndf-rebuild-data-and-smells.md *
  ndf-rebuild-scripted-flow.md *
  ndf-rebuild-measure.md *         物差し・前後の値・打ったコマンド
  ndf-rebuild-review-scope.md *
  ndf-knowledge-and-kiro.md        「仕様化の扱い」を issues/README.md へ向ける
  README.md                        索引に 6 本を足す
issues/README.md                   完了後の扱い
docs/glossary/glossary.json        ndf records spec finalize が書く
docs/glossary/ndf-rebuild.md       ndf records glossary render が書く
scripts/measure/
  ai-branching.py                  形ごとの数え方
  ai-branching-rules.json          規則の番号と形を足す
  ai-branching-measures.json *     規則の番号 → G1〜G3 と後の形の出所
  claude-p-usage.py                後の形を ndf records usage report から読む
scripts/tests/
  test_ai_branching.py *
  test_claude_p_usage.py
plugins/ndf/…                      作り直しの決定を指すコメントの行だけ
```

依存の向き: `ai-branching.py` は同じディレクトリの 2 つの JSON と、`plugins/ndf/contracts/schemas/route-chosen.schema.json`（ファイルとして読む）と、`jsonschema` だけに依存する。`plugins/ndf/src` を import しない。`claude-p-usage.py` は `ndf` を外部コマンドとして呼ぶ。確定仕様は互いにリンクで指し、`ndf-rebuild.md` が索引になる。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `scripts/measure/ai-branching.py` | 変更 | 前後の形の記録から規則の番号ごとの回数と G1〜G3 を出す | 規則の表・分類の表・選択の記録のスキーマ・`jsonschema` | モジュール | `load_rules`（新設）・`load_measures`（新設）・`count_choices`（新設）・`summarize`（新設）・`scan_transcript`（変更）・`count_transcripts`（変更）・`main`（引数 `--until` を足す） |
| `scripts/measure/ai-branching-rules.json` | 変更 | 数える規則（正規表現・Tool・文）に `rule` と `form` を持たせる | — | データ | — |
| `scripts/measure/ai-branching-measures.json` | 新設 | 規則の番号を物差しへ寄せ、後の形の出所を決める | — | データ | — |
| `scripts/measure/claude-p-usage.py` | 変更 | 11 系の使用量を後の形として読む | `ndf records usage report` | モジュール | `read_usage_report`（新設） |
| `scripts/tests/test_ai_branching.py` | 新設 | 前後の見本で同じ回数に数えることを縛る | `ai-branching.py` | テスト | — |
| `scripts/tests/test_claude_p_usage.py` | 変更 | 後の形の読み方を縛る | `claude-p-usage.py` | テスト | — |

## 確定仕様化

満たす決定: 決定 1〜7、受け入れ条件 1〜6・11

### 処理の手順

| 手順 | 入力 | 出力 | 終わりの条件 |
| --- | --- | --- | --- |
| 1. 決定を分ける | 移す 20 本の `### 決定 N` の見出しと注記（「採らない」「置き換えた」・取り消し線） | 決定ごとの「生きている / 置き換えた（置き換え先）/ 戻した」の表（作業ファイル。コミットしない） | 移す文書の見出しの全件が表にある |
| 2. 確定仕様を書く | 移す 20 本・手順 1 の表 | 6 本。各本 500 行以下 | `plan-to-spec` の削除・変換ルールを当て、開発の履歴・子の割り当て・途中の書き直しを落としている |
| 3. 番号をそろえる | 6 本の決定と不変条件の見出し | `scripted-flow` の決定 65 を 130 にし、不変条件の重なりも最大の次へ振り直す。`ndf-rebuild.md` の「番号の対応表」に（元の系統・元の番号・確定仕様・番号）を書く。元の文書はファイル名で書かず、表の頭に消す前のコミットの `issues/` の固定リンクを 1 つ置く | 設計文書のテスト設計の 2 の `grep` が 0 行 |
| 4. 記号の表を置く | 作り直しの子の記号と Issue の番号（`gh issue list --label`・#3388 のサブ Issue） | `ndf-rebuild.md` の「子の記号」の表 | 6 本の記号がすべて表にある。前の企画の子は番号で書く |
| 5. コメントを書き換える | `git grep -nE '決定 ?[0-9]+' -- plugins/ndf/src plugins/ndf/tests scripts` の 316 行 | 作り直しの決定を指す行を `docs/specifications/ndf-rebuild-<主題>.md の決定 N` の形へ | 作り直しの決定を指す行がすべてファイル名を持つ。ほかの課題の決定を指す行は変えない |
| 6. 文書の参照を書き換える | 要求仕様の検証手段の `git grep` の当たり（用語集を除く） | 確定仕様へのリンク | 当たりが用語集の `pending_source` と `scripts/ndf-next-parity/` だけ |
| 7. 開いている Issue のリンクを固定する | `gh search issues --state open` で 36 本のファイル名を本文に持つ Issue | 本文の `blob/<ブランチ>/issues/<36 本のどれか>` を `blob/<消す前の main の SHA>/issues/…` に替えた本文 | 開いている Issue の本文に、ブランチを指す 36 本へのリンクが無い |
| 8. 消す | 6 本と 36 本 | `ndf records spec finalize --spec <6 本> --remove <36 本>`（語の SSOT が決まらなければ `--source-of <語>=<ファイル>`） | 36 本が消え、用語集の `pending_source` が確定仕様を指す。`ndf records glossary render --check` が `ok` |
| 9. 扱いを 1 か所にする | `issues/README.md`・`ndf-knowledge-and-kiro.md` | `issues/README.md` に「確定仕様へ移した後に `ndf records spec finalize` で消す。`old/` は 11.0.x までに移したものの保管で、新しく足さない」。`ndf-knowledge-and-kiro.md` の「仕様化の扱い」は `issues/README.md` を指す | 2 か所が同じ扱いを指す |

- 手順 7 は手順 8 の前に行う（設計文書の処理の流れ）。SHA は手順 8 のコミットの親の `origin/main` で、PR の作業の中で 1 回だけ取る
- 手順 5 で系統を読み取れない行（2026-11-21 の棚卸しで 58 行）は、前後 10 行のコメントと同じファイルの他の決定の指しから決め、決められなければ書き換えず PR の本文に行を挙げる

### 異常系の処理

| 手順 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | 番号が重なる | 振り直しの漏れ | 手順 3 の `grep` | 止める | 重なる番号が出る | 直して打ち直せる |
| 7 | 本文の書き戻しの間に他者が書いた | 同時の編集 | 書き戻しの後に本文を取り直して比べる | 止める | 差分を PR の作業の記録に残す | 取り直した本文で打ち直せる |
| 8 | 語の SSOT が決まらない | 語が 2 本以上の確定仕様に現れる | `ndf records spec finalize` | 呼び出し元へ返す | `summary` に語と候補 | `--source-of` を足して打ち直せる |
| 8 | 参照が残る | 手順 5・6 の漏れ | `scripts/check-markdown-links.py`・文書の古さの検査 | 止める | 検査の当たり | 直して打ち直せる |

## 前後の形の計測

満たす決定: 決定 8・9・10、受け入れ条件 7〜10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load_rules` | `scripts/measure/ai-branching.py` | 新設 | `load_rules(path: pathlib.Path) -> list[dict]` | 規則の表を読み、`rule`（`R` と数字）・`form`（`before` / `after`）・`kind`（`bash` / `tool` / `phrase`）を確かめて返す |
| `load_measures` | 同上 | 新設 | `load_measures(path: pathlib.Path) -> dict[str, dict]` | 規則の番号ごとの `measure`（`G1` / `G2` / `G3` / `none`）と `after_source`（`records` / `transcripts`）を返す |
| `count_choices` | 同上 | 新設 | `count_choices(files: list[pathlib.Path], since: str, until: str, schema: dict) -> dict` | `progress.jsonl` の `route_chosen` の行をスキーマで確かめ、規則の番号と `chooser` ごとに数える。形の合わない行は `rejected` に数える |
| `summarize` | 同上 | 新設 | `summarize(counts: dict, measures: dict) -> dict` | 形ごとの規則の番号の回数から G1〜G3 を足す。後の形は `after_source` の出所の回数だけを使い、`chooser` が `conductor` の回だけを入れる |
| `scan_transcript` | 同上 | 変更 | `scan_transcript(args: tuple[str, str, str, list[dict]]) -> dict` | 渡された規則で当たりを数え、当たりを規則の番号と形で返す（前は規則の名前で返した）。`until` より後の行を数えない |
| `count_transcripts` | 同上 | 変更 | `count_transcripts(roots: list[str], since: str, until: str, jobs: int, rules: list[dict]) -> dict` | `until` と規則を `scan_transcript` へ渡し、規則の番号と形ごとに合計する |
| `read_usage_report` | `scripts/measure/claude-p-usage.py` | 新設 | `read_usage_report(since: str, until: str) -> dict` | `ndf records usage report --since --until` を打ち、結果 JSON の `items` を後の形の使用量として返す |

- `count_choices`
  - 事前条件: `schema` は `route-chosen.schema.json` を読んだ値
  - 事後条件: 返りは `{"by_rule": {<R>: {<chooser>: 回数}}, "rejected": 件数, "files": 件数}`
  - 副作用: ファイルを読むだけ
- `read_usage_report`
  - 副作用: 外部コマンド `ndf` を 1 回打つ

`main` の出力は今のキー（`since`・`progress`・`ledger`・`transcripts`）に `until` と `forms` を足す。`forms` は `{"before": {"by_rule": …, "measures": {"G1": n, "G2": n, "G3": n}}, "after": {…}}` である。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rules` | 規則の行に `rule` か `form` が無い・値が語彙の外 | 表の書き誤り | 読むとき | 止める | 標準エラーに行の番号と欠けた項目、終了コード 2 | 表を直して打ち直せる |
| `load_measures` | 規則の表にある番号が分類の表に無い | 片方だけ足した | `main` で 2 つを照らす | 止める | 欠けた番号、終了コード 2 | 同上 |
| `count_choices` | 行が JSON でない・スキーマに合わない | 書き手の誤り・形の変更 | 行ごとに確かめる | 既定値で続ける（数えずに `rejected` に足す） | `forms.after.rejected` に件数 | 不要 |
| `count_choices` | スキーマのファイルが無い | 配置の変更 | `main` で読むとき | 止める | パス、終了コード 3 | 配置を直して打ち直せる |
| `read_usage_report` | `ndf` が無い・0 以外で終わる・JSON でない | 導入されていない・期間に記録が無い | 呼び出しの後 | 呼び出し元へ返す | 結果に `after: {"error": <理由>}` を載せ、ほかの値は出す。終了コード 0 | 導入してから打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 規則の行の `rule`・`form`・`kind` | 設定値 | `scripts/measure/ai-branching-rules.json` | 前の 25 行は `form: before`。`PHRASES`・`TOOLS` もこの表の `kind: phrase`・`kind: tool` の行へ移す |
| 規則の番号の `measure`・`after_source` | 設定値 | `scripts/measure/ai-branching-measures.json` | scripted-flow の洗い出しの判定（できる → G1、一部できる → G2、できない（数えられる 2 規則）→ G3、ほか → none） |
| `--until` | 引数 | `main` | 打った日（UTC） |
| `SCHEMA` | 定数 | `ai-branching.py` | `plugins/ndf/contracts/schemas/route-chosen.schema.json`（リポジトリの根からの相対） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `count_choices` | `ndf.core.plan.records.recorded_choices` | `grep -rn "def recorded_choices" plugins/ndf/src` | 使えない理由: import すると計測が配るコードのバージョンに縛られる（決定 9）。行を読んだ後の確かめは `jsonschema` を使う |
| `read_usage_report` | `ndf records usage report` | `ndf records usage report --help` | 使う（外部コマンドとして） |
| `load_rules` | 今の `RULES` の読み込み（モジュールの頭） | `ai-branching.py` の 35〜38 行 | 関数へ移して確かめを足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_rules` | 前と後の行が形ごとに分かれる | 規則が 0 行の形 | `rule` の無い行で終了コード 2 |
| `count_choices` | `conductor`・`script`・`human` の行を規則の番号ごとに数える | `since` と `until` の当日の行を含む | スキーマに合わない行は `rejected` に入り、数えない |
| `summarize` | 前の形と後の形で同じ規則の番号が同じ回数になる（テスト設計の 8） | `after_source` が `records` の規則は会話の記録の当たりを数えない | `chooser` を読み捨てると `script` の回が G に入って落ちる |
| `count_transcripts` | 前の形と後の形のコマンドを規則の番号で返す | `until` の翌日の行を数えない | — |
| `read_usage_report` | 結果 JSON の `items` を返す | 期間に記録が 0 件 | `ndf` が無いと `error` を載せて続ける |

## 測り直し

満たす決定: 決定 10、受け入れ条件 9・10

| 物差し | 打つコマンド | 前の値の期間 | 後の値の期間 |
| --- | --- | --- | --- |
| G1〜G3 | `python3 scripts/measure/ai-branching.py --since 2026-11-18 --until <測る日>`（前の値は `--since 2026-10-14 --until 2026-11-13` で打ち直し、表の値と同じかを確かめる） | 2026-10-14〜11-13 | 2026-11-18〜測る日 |
| U1・U3 | `python3 scripts/token-usage.py --min-version 11.0.0 --until <測る日>T23:59:59Z --by version` と `python3 scripts/measure/claude-p-usage.py` | 2026-09-25 の #1142 のコメント | 同上 |
| U2 | `claude-p-usage.py` の後の形（`ndf records usage report`） | 同上 | 同上 |

結果は `ndf-rebuild-measure.md` の「前と後の値」の表（物差し・前の値・後の値・期間・コマンド・測れなかった理由）と、#1142 のコメントに同じ表で残す。U1 の行に目標 1.14M との差を書く。30 日の値は 2026-12-18 以降に同じコマンドで足す（リリース後の確認の計画）。
