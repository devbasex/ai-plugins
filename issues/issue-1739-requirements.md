# #1739: instructions-check: 開発版の版数の接尾辞で AGENTS.md が伸び、PR の CI で上限ちょうどだった読み込み量が配布の sync で上限を超えて止まる

正は課題の本文（#1739）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 目的
## 依頼（原文）

> ## 何を見つけたか
>
> **開発版の版数を上げると `AGENTS.md` の本文が伸び、Pull Request の CI では通っていた指示書の読み込み量の検査（`instructions-check.py`）が、配布の `sync` で初めて落ちる。**
>
> `AGENTS.md` の本文は現行の版数（「NDFプラグインは…主要プラグインです（v10.17.59）」）を持ち、`CLAUDE.md` は `@AGENTS.md` で取り込む。検査は `CLAUDE.md` の読み込み量を取り込み先まで含めて数え、上限は `.ndf/instructions.json` の `budget.bytes`（24,000）である。
>
> | コミット | 何をしたか | `CLAUDE.md` + `AGENTS.md` のバイト数 | 検査 |
> | --- | --- | ---: | --- |
> | `13dffa70`（スプリント PR 1732 のマージ） | スプリントの変更 | 24,000 | 通る（上限ちょうど） |
> | `44de5b4d`（Release: ndf v10.17.60-dev.1） | 版数 `v10.17.59` → `v10.17.60-dev.1`（+6 バイト） | 24,006 | 落ちる: `CLAUDE.md: 読み込みの量が上限を超えた（24,006 > 24,000 バイト）` |
> | `9bf79643` | 配布の worker が `CLAUDE.md` を 1 行削った | 23,991 | 通る |
>
> 開発版の接尾辞（`-dev.<n>`）は版数を最大で 6 バイト以上伸ばす。上限との差が接尾辞の長さより小さいまま Pull Request がマージされると、配布の工程で必ず落ち、配布の修正（judge → fix）が指示書の本文を削ることになる。
>
> ## どこで見つけたか
>
> - スプリント m1385（10.17.60）の開発版の配布: `~/.local/state/ndf/sv/sprint-m1385/7-release-r2-state/04-sync.out`（`supervise-sync-check` の `instructions` が failed）
> - 検査: `plugins/ndf/scripts/instructions-check.py`、上限: `.ndf/instructions.json` の `budget.bytes`
> - 版数を書く行: `AGENTS.md` の「NDFプラグインについて」の節
>
> ## なぜこの変更の範囲外なのか
>
> スプリント m1385 は cross-refactoring の時間の上限・担当の振り替え・CI 待ち・mcp-serena のテストが対象で、指示書の検査と配布の版数の扱いは受け入れ条件に含まれない。振り返りで拾った取りこぼしである。根拠: Value 1（開発版のチャネルで早く届ける工程を止めない）・Value 6（配布の工程で本文を削る現象の手当てでなく、検査の時点か版数の置き場で直す）（MVV 版 2）
>
> ## 直さないと何が起きるか
>
> - 上限に近い状態でマージしたスプリントは、開発版の配布で毎回止まり、配布の修正が判断なしに指示書の本文を削る（今回は 1 行。何を削ったかを人が承認していない）
> - 正式版で接尾辞を外すと 6 バイト戻るため、開発版の配布だけが落ち、原因が見えにくい
>
> ## 考えられる直し方（判断は設計で）
>
> - 検査の上限の判定から、版数の接尾辞の分（または版数の行そのもの）を除く
> - Pull Request の CI で、接尾辞を付けた版数を仮に当てた後の量でも検査する（余白を持たせる）
> - `AGENTS.md` の本文から版数を外す（版数の正本は `plugin.json` の `version`）
>
> ## 由来
>
> PR #1732（スプリント m1385 の振り返りで拾った取りこぼし）
>

## 目的

- **Pull Request の CI で指示書の検査（`instructions-check.py`）が通ったなら、そのマージの後の開発版の配布（版数を `-dev.<連番>` 付きへ上げる `bump` → `sync-check`）でも、同じ検査が読み込み量の上限で落ちない状態にする。** 落ちるなら、配布の時点ではなく Pull Request の時点で落ちる
- 配布の修正（judge → fix）が、人の承認なしに指示書の本文を削って上限へ収める経路を無くす

## 前提

- 前提 1: 開発版の版数は `docs/versioning-and-distribution.md` の「版の付け方と開発版の配布」のとおり、次に出す正式版の版数へ `-dev.<連番>` を付けた形である（例: `10.17.63` → `10.17.64-dev.1`）。正式版で接尾辞を外す
- 前提 2: 配布の `bump` で伸びる量は、接尾辞（`-dev.1` で 6 バイト、連番が 2 桁なら 7 バイト）と、版数の桁の繰り上がり（例: `10.17.99` → `10.17.100` で 1 バイト）の和である。`rc`（公開前の確認版）の接尾辞も同じ扱いにする
- 前提 3: 上限（`.ndf/instructions.json` の `budget.bytes` = 24,000）の値そのものは、この課題で変えない。値を変えて余白を作る直し方は、根本原因（版数の置き場か検査の時点）を直さないため採らない（Value 6）
- 前提 4: 直し方（検査から版数の分を除く / PR の CI で接尾辞を当てた後の量を検査する / `AGENTS.md` の本文から版数を外す / その組み合わせ）は設計で決める。この仕様はどれを選んでも判定できる受け入れ条件だけを持つ
- 前提 5: 2026-10-06 時点の手元の量は `CLAUDE.md` 23,985 / 24,000 バイト（余白 15 バイト）で、次の開発版の `bump` では落ちない。落ちるのは余白が伸びる量より小さいままマージされたときである

## 対象範囲

含む:
- 指示書の読み込み量の検査と、開発版の配布の `bump` が書き換える版数の関係
- `AGENTS.md`（`CLAUDE.md` が `@AGENTS.md` で取り込む）の版数の行の扱い
- 上の変更に合わせた、版数を持つ箇所の突き合わせ（`docs/versioning-and-distribution.md` の「版数を持つ 15 箇所」・`scripts/check-doc-staleness.py`・`scripts/validate-runtime-plugins.sh`・`release-steps.py` の `bump`）の追随

含まない:
- 上限の値（`budget.bytes`）の変更
- 指示書の本文を削って余白を作ること（現行の量は前提 5 のとおり通る）
- 配布の修正（judge → fix）の判断の一般的な見直し（指示書以外の失敗の扱い）
- 指示の数・リリース済み版の段落・@インポートなど、読み込み量以外の検査の観点
- 型・クラスの追加と変更（設計のクラス図の対象が無い。`check-doc-staleness.py` の照合の一覧から 1 項目を外し、`plan_bump` から 1 行を外すだけで、型は変わらない）
- 正式版の配布の工程の変更（接尾辞を外すと量は減る方向にしか動かない）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | Pull Request の CI が指示書の検査を通した | `develop` 宛の Pull Request の push（`.github/workflows/runtime-plugin-validate.yml`） | Pull Request が赤になり、作者が直す | — |
| E2 | Pull Request を `develop` へマージした | 人か `merge-when-green` のマージ | — | E1 |
| E3 | 開発版の配布の `bump` が版数を `-dev.<連番>` 付きへ書き換えた | 配布の工程（`release-steps.py bump`） | 書き換えられない箇所は手で直す一覧に載る | E2 |
| E4 | 配布の `sync-check` が指示書の検査を走らせた | 配布の工程（`supervise-sync-check`） | judge → fix へ回る。**ここで読み込み量で落ちるのがこの課題の現象** | E3 |
| E5 | 配布の修正が指示書の本文を削った | E4 の失敗 | — | E4（この課題の後は起きない） |
| E6 | 正式版の配布で接尾辞を外した | 正式版の配布の工程 | — | E3 |

E4 は E1 と同じ検査を、E3 で版数が伸びた後の本文に掛ける。E1 の時点で E3 の伸びを見ていないことが、E1 が通り E4 が落ちる原因である。

## 受け入れ条件

- [ ] 1: 読み込み量が上限ちょうど（`budget.bytes` と等しい）の指示書を持つ一時リポジトリで、Pull Request の CI と同じ検査（`.github/workflows/runtime-plugin-validate.yml` が走らせるもの）が終了コード 0 で通ったなら、続けて `release-steps.py` の `bump` で ndf の版数を `X.Y.Z` → `X.Y.(Z+1)-dev.1` へ上げた後の `instructions-check.py --root .` も読み込み量の指摘（`read-size-budget`）を出さない。「PR の CI が通り、配布で落ちる」組み合わせが起きない（PR の CI の側で先に落とすか、配布の側で落ちないようにするかは設計が決める）
- [ ] 2: 受け入れ条件 1 は、接尾辞の連番が 2 桁（`-dev.10`）のときと、PATCH の桁が繰り上がるとき（`X.Y.99` → `X.Y.100-dev.1`）にも成り立つ
- [ ] 3: 版数の伸びと無関係に上限を超えた指示書（例: 版数の行以外へ 100 バイト足した）は、これまでどおり `read-size-budget` で終了コード 1 になる（検査を緩めて通すのではない）
- [ ] 4: `.ndf/instructions.json` を持たないリポジトリと、`budget` を宣言しないリポジトリで、`instructions-check.py` の結果（終了コードと指摘）が変更の前と変わらない
- [ ] 5: 版数の行を持たない指示書のリポジトリ（ai-plugins 以外の利用者のリポジトリ）でも検査が動き、ai-plugins の版数の行の書き方（「主要プラグインです（v…）」）を前提にした分岐を既定に持たない。ai-plugins に固有のものは `.ndf/` の宣言か引数で渡す
- [ ] 6: 変更の後のリポジトリで、`python3 plugins/ndf/scripts/instructions-check.py --root .`・`python3 scripts/check-doc-staleness.py`・`bash scripts/validate-runtime-plugins.sh` がいずれも終了コード 0 で終わる
- [ ] 7: 版数を持つ箇所の一覧（`docs/versioning-and-distribution.md` の「版数を持つ 15 箇所」）と、`bump` が書き換える箇所と、チェックが突き合わせる箇所が一致している（`AGENTS.md` の版数を外すなら 3 つから同時に外れ、残すなら 3 つに同時に残る）
- [ ] 8: 受け入れ条件 1〜5 を確かめる自動テストが `plugins/ndf/scripts/tests/` にあり、全体テストで通る。`.md` の文言を照合するテストは足さない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 開発版の配布が、指示書の読み込み量を理由に judge → fix へ回らない（受け入れ条件 1 が成り立てば満たす） |
| 移行性 | 利用者のリポジトリの `.ndf/instructions.json` を書き換えなくても、変更の前と同じ判定で動く（受け入れ条件 4）。宣言に項目を足すなら、無いときの既定は変更の前の振る舞いと同じにする |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `instructions-check.py` の引数・終了コード・`.ndf/instructions.json` の既存の項目は変わらない。宣言に項目を足すなら任意の項目として足す（互換あり） |
| データ | 無し |
| 既存の振る舞い | 読み込み量の判定（設計の選択次第で、版数の分の数え方か、検査する時点が変わる）。設計が `AGENTS.md` から版数を外すなら、`bump` と版数のチェックの対象が 1 箇所減る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4` |
| 静的解析・チェック | `python3 plugins/ndf/scripts/instructions-check.py --root .`、`python3 scripts/check-doc-staleness.py`、`bash scripts/validate-runtime-plugins.sh`、`claude plugin validate .` |
| 手動確認 | このスプリントの次の開発版の配布で、`supervise-sync-check` の `instructions` が failed にならないことを `~/.local/state/ndf/sv/<スプリント>/…/04-sync.out` で確かめる（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 検査は `plugins/ndf/scripts/instructions-check.py` と `instructions_lib/`、版数の書き換えは `release-steps.py`。観点・強さは `data/instruction-criteria.json` と `.ndf/instructions.json` が持ち、手続きだけをスクリプトに置く（`instructions-check.py` の冒頭の説明） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。外部コマンドと自分の入出力の形を書く前に実行して確かめる（`AGENTS.md` のベストプラクティス） |
| テスト戦略 | 一時リポジトリを作り `bump` と検査を順に走らせる実行テストで受け入れ条件 1〜5 を見る。文言の照合はしない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テスト、版数の突き合わせのチェックの実行 |
| 確認してから行う | `.ndf/instructions.json` の書き換え（C7。設計の承認で扱う）、`AGENTS.md` の運用の節の書き換え（C7） |
| 行わない | 上限の値の変更、指示書の本文を削って余白を作ること、配布の取得元・版の規則の変更（P1） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 3 つの直し方（版数の分を検査から除く / PR の CI で接尾辞を当てた後を検査する / `AGENTS.md` から版数を外す）のどれを採るか | 設計（設計の承認で人が確かめる） | 設計 PR |
| `AGENTS.md` から版数を外すなら、`AGENTS.md` の「NDFプラグインについて」の書き換え（C7 に当たる）を設計の承認で一緒に承認するか | 人（設計の承認） | 設計 PR |
