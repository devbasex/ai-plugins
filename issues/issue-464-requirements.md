# #464: cross-refactoring のテストの宣言が整形・静的解析を含まず、違反が継続的統合まで残る

正は課題の本文（#464）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> **このリポジトリの宣言（`.ndf/project.json` の `test.suites`）へ整形の suite（`kind: lint`。例 `ruff format --check {paths}`）を足し、
> 項目ごとの範囲テストで整形の違反を捕まえる。** 言語別の整形・静的解析の規約を NDF が持つことは #1691 で扱う。
>
> （「方針（2026-10-03 利用者の決定）」の節）

> 直すときは、項目ごとの検証で `check-script-structure.py` と `scripts/check-lint.sh` も走らせることを受け入れ条件に含める。
>
> （「振り返りからの追記（2026-09-29）」の節）

> - [ ] `.ndf/project.json` の `test.suites` に整形の suite（`kind: lint`）があり、`scope_command` が項目の範囲（`{paths}`）で走る
> - [ ] 整形の違反を入れた項目が、cross-refactoring の範囲テストで失敗になる
> - [ ] `init` が継続的統合の検査と宣言を突き合わせ、宣言に無い検査を知らせる
>
> （起票時の「受け入れ条件」の節）

> * そのプロジェクトに整形・静的解析の規約やツールがあればそれを利用する
>
> （2026-09-07 の利用者のコメント https://github.com/devbasex/ai-plugins/issues/464#issuecomment-5570162076 の 1 行目。2 行目以降は #1691 へ切り出した）

## 目的

- cross-refactoring の項目が整形・静的解析の違反を入れたとき、**その項目の検証の時点で失敗として扱われ**、Pull Request の継続的統合や push 前の検査（`.githooks/pre-push`）で初めて落ちる状態をなくす
- 継続的統合が走らせる検査のうち宣言に無いものを、`init` の時点で利用者へ知らせる。宣言の書き漏れに、継続的統合を見る前に気づけるようにする

## 前提

- 前提 1: 整形・静的解析の検査そのもの（何を使うか・設定）は、このリポジトリに既にある `scripts/check-lint.sh` と同じもの（ruff format --check・ruff check・shellcheck -S warning、版は `uv.lock` の lint グループ）を使う。新しい検査を足さない
- 前提 2: 宣言へ足す suite の形（1 つの suite で 3 つの検査を走らせるか、検査ごとに suite を分けるか、`check-lint.sh` にパスを受けさせるか）は `design` が決める。どの形でも受け入れ条件 1〜4 を満たせばよい
- 前提 3: 範囲テストへ入るファイルは、今の `lint_runs` と同じく「項目のコミットが変えたファイルのうち、作業ディレクトリに残り、suite の `paths` に当たるもの」である。消したファイルは入らない
- 前提 4: 全体テスト（着手前・危険フラグ・手元の最終ゲート）は、今と同じく宣言の全 suite の `command` を走らせる。足した lint の suite の `command` もそこに入る
- 前提 5: `init` の突き合わせは知らせるだけで、起動を止めない（宣言の書き漏れは利用者が直す。止めると宣言を持たないリポジトリで cross-refactoring が使えなくなる）
- 前提 6: `init` が読む継続的統合は、宣言の `ci.provider` が `github-actions` のものだけを対象にする。ほかの provider と、宣言に `ci` が無いときは「突き合わせられなかった」と知らせて続ける
- 前提 7: `.ndf/project.json` の書き換えは共通原則の C7（利用者の設定の書き換え）に当たる。方針は利用者が決めた（2026-10-03）が、書き換えを含む Pull Request のマージは MVV の判定に任せず、人の承認を取る

## 対象範囲

含む:
- このリポジトリの `.ndf/project.json` の `test.suites` へ、整形・静的解析の suite（`kind: lint`）を足す
- その suite が範囲テストで正しく働くのに要る変更（`paths` の指定、必要なら `scripts/check-lint.sh` にパスを受けさせる変更）
- cross-refactoring の `init` が、継続的統合の検査と宣言を突き合わせて知らせる処理
- 上の振る舞いを確かめる自動テスト

含まない:
- 言語別の整形・静的解析の規約を NDF が持ち、規約の無いリポジトリへ移植すること（#1691）
- cross-review の修正コミットが整形を通さない件（#1507）
- `--baseline-test` / `--round-test` の説明の変更（起票時の案 A。採らなかった）
- push の直前にオーケストレーターが整形を掛けてコミットを積むこと（起票時の案 C。採らなかった）
- 範囲テストの組み方（`lint_runs` / `scope_runs`）の変更。今の仕組みで足りない所が見つかったときは `design` で人へ戻す
- 取り消し（revert）の後に push が拒まれて記録が残らない件（#1648）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `init` が宣言からテストの戦略を組み、lint の suite を範囲テストの候補に入れた | `/ndf:cross-refactoring` の起動 | 宣言が読めなければ今と同じく止まる | — |
| E2 | `init` が継続的統合の検査と宣言を突き合わせ、宣言に無い検査を知らせた | E1 の後 | 継続的統合の定義が読めなければ「突き合わせられなかった」と知らせて続ける（前提 5・6） | E1 |
| E3 | 着手前の全体テストが lint の suite の `command` を走らせた | 着手前のテスト | 違反があれば今と同じく既存失敗として記録する | E1 |
| E4 | 実装担当が項目のコミットを積んだ | 計画の項目 | — | E3 |
| E5 | 項目の検証が、コミットの変えたファイルのうち lint の suite の `paths` に当たるものへ範囲テストを走らせた | E4 | 違反があれば項目の検証が失敗し、今の修正・取り消しの流れへ入る | E4 |
| E6 | 手元の最終ゲートが lint の suite を含む全体テストを走らせた | 全項目の後 | 違反があれば最終ゲート修正へ回る | E5 |
| E7 | オーケストレーターが作業ブランチを push した | E6 の後 | push 前の検査が整形で拒む（この課題が無くす失敗） | E6 |

E2 の「継続的統合の検査」が何を指すか（ワークフローのジョブか、step の `run:` のコマンドか、`ci.required_checks` か）と、
宣言と「一致した」とみなす規則は未決（`design` が決める）。

## 用語

| 用語 | 意味 |
| --- | --- |
| 範囲テスト | 宣言の `scope_command` の `{paths}` に項目の範囲を入れて走らせるテスト。lint の suite では項目のコミットが変えたファイルを入れる |
| 全体テスト | 宣言の suite の `command` を範囲を絞らずに走らせるテスト |

## 受け入れ条件

- [ ] 1. `.ndf/project.json` の `test.suites` に `kind: lint` の suite があり、その `scope_command` の `{paths}` を埋めて走らせると、ruff format --check・ruff check・shellcheck の違反を検出する（`check-lint.sh` が見る 3 つの検査が、どれも範囲テストのどれかの suite で走る）
- [ ] 2. 前提: 作業ディレクトリに cross-refactoring の状態ファイルがあり、項目のコミットが Python のファイルを ruff format の違反のある形で変えた
      操作: 項目の検証（`verify_runs` から組んだ範囲テスト）を走らせる
      結果: lint の suite の範囲テストが終了コード 0 以外を返し、項目の検証が失敗になる
- [ ] 3. 前提: 項目のコミットが `.md` や `.json` など整形の対象外のファイルだけを変えた
      結果: lint の suite の範囲テストは組まれないか、組まれても終了コード 0 で通る（`ruff format --check` に `.sh` を渡すと構文の誤りで終了コード 2 になることを 2026-10-03 に実測した。対象外のファイルで失敗しない）
- [ ] 4. 違反の無いこのリポジトリの HEAD で、宣言の lint の suite の `command`（全体テスト）が終了コード 0 で通り、`bash scripts/check-lint.sh` と同じファイルを対象にする
- [ ] 5. 前提: 継続的統合のワークフローが走らせる検査のうち 1 つが、宣言の suite のどれにも当たらない
      操作: cross-refactoring の `init` を走らせる
      結果: その検査の名前（ワークフローのファイルと検査の識別子）を挙げて「宣言に無い」と知らせ、`init` は止まらずに終わる
- [ ] 6. 宣言の suite が継続的統合の検査をすべて覆うとき、`init` は宣言に無い検査を知らせない（誤った知らせを出さない）
- [ ] 7. 宣言に `ci` が無いか、`ci.provider` が `github-actions` でないとき、`init` は突き合わせられなかったことを知らせて止まらずに終わる
- [ ] 8. 退行しない: 既存の suite（pytest・script-structure）の範囲テストと全体テストの組み方と結果が変わらず、`uv run --frozen --project . --all-extras pytest . -q -n 4` が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | lint の範囲テスト 1 回は、項目が変えたファイルだけを見るため数秒で終わる（`lint.yml` の全体で 16 秒の実測より短い）。実装後に 1 回測って記録する |
| 運用・保守性 | `init` の知らせは状態ファイルか `init` の出力に残り、後から読める |
| システム環境 | 検査のツールは `uv run --frozen --project . --only-group lint` から起動し、`uv.lock` の版を使う（`check-lint.sh` と同じ） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `init` の出力に知らせが加わる。引数と終了コードは変わらない |
| データ | `.ndf/project.json` の `test.suites` に suite が増える（C7。前提 7）。`analysis.written.test` のハッシュが手で変えた値とずれる扱いは `design` が確かめる |
| 既存の振る舞い | このリポジトリで cross-refactoring・`supervise.py` の検査プランを走らせたとき、項目の検証と全体テストに lint の suite が加わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `bash scripts/check-lint.sh`、`python3 scripts/check-script-structure.py` |
| 範囲テストの実地 | 違反を入れた Python ファイルのパスで lint の suite の `scope_command` を手で走らせ、終了コードを見る（受け入れ条件 2・3） |
| 手動確認 | 次に cross-refactoring を走らせたスプリントで、push 前の検査が整形で拒まないことを確かめる（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | テストの戦略の解釈は `plugins/ndf/scripts/lib/test_strategy.py`、cross-refactoring の手順は `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/`。宣言の形は `plugins/ndf/skills/development-workflow/schemas/project.schema.json` |
| コーディング規約 | `AGENTS.md`・`scripts/check-lint.sh`・`scripts/check-script-structure.py` |
| テスト戦略 | 範囲テストの組み立てと `init` の突き合わせは単体テスト（pytest）で見る。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`check-lint.sh` と `check-script-structure.py` の実行 |
| 確認してから行う | `.ndf/project.json` の書き換え（C7）、宣言のスキーマ（`project.schema.json`）の変更、`check-lint.sh` の引数の契約の変更（CI と pre-push が同じものを呼ぶ） |
| 行わない | ai-plugins の検査を NDF の既定に埋め込むこと（Value 5）、#1691・#1507 の範囲の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| lint の suite の形（1 つか 3 つか、`check-lint.sh` にパスを受けさせるか、拡張子の無い shebang のシェルスクリプトを範囲テストで拾うか） | `design`（設計の承認で人が確かめる） | 設計 PR |
| `init` が突き合わせる「継続的統合の検査」の単位と、宣言と一致したとみなす規則 | `design` | 設計 PR |
| `--ci-check` で最終ゲートを継続的統合に任せる起動で、最終ゲート修正のコミットが push 前に整形の違反で拒まれる経路（2026-09-30 の再発の形）が、この変更で塞がるか。塞がらなければ #1507 と同じ層で直すか | `design` | 設計 PR |

## 起票時の記録

方針（2026-10-03）が決まる前の調査と候補。要求は上の節が持ち、ここは経緯として残す。

### 何を見つけたか

**`cross-refactoring` が走らせるテストの宣言（`.ndf/project.json` の `test`）は整形・静的解析を
持たないため、適用担当が入れた整形の違反がそのまま Pull Request へ残り、継続的統合で落ちる。**

テストの走らせ方は `.ndf/project.json` の `test` が決める（#1334）。`SKILL.md` の引数の表（:74）は
`--baseline-test` の既定を「宣言の `test` を読む」とし、`--round-test` は宣言に `test` が無いときの
逃げ道である（:75）。**宣言の suite は種別 `kind`（`test` / `lint`）を持つ**（#1483。
`development-workflow/schemas/project.schema.json` の `Suite`）。このリポジトリの宣言は pytest と構造チェック
（`kind: lint`、#1668）の 2 件で、整形・静的解析（`scripts/check-lint.sh` の ruff format / ruff check / shellcheck）は
宣言に無い。`check-lint.sh` は範囲（`{paths}`）を受けないため、そのまま `scope_command` にできない。

**項目ごとの検証は宣言の `scope_command` から組む範囲テストで走る。** 宣言がテストだけなら、整形の違反は
項目の時点でも最終ゲートでも拾えない（継続的統合で判定する起動を除く）。

### どこで見つけたか

`takemi-ohama/with-bi` で `--baseline-test "uv run pytest -q"` を渡して実行した。
3 項目が適用され、2 ラウンドとも承認で収束した。

**そのうち 1 項目が追加した現状固定テストが、行長 120 を超えていた。**

```
test  整形を検査  would reformat /home/runner/work/with-bi/with-bi/scripts/scheduled_query/test_deploy.py
```

このリポジトリの変更検証は `uv run black --check --diff scripts` を実行する
（`pyproject.toml` の `[tool.black]` が行長 120 を持つ）。渡したテストが
`pytest` だけだったため、**適用の各コミットでは通り、Pull Request の継続的統合で初めて落ちた**。

### なぜ適用担当が直せないか

`SKILL.md` のアンチパターンが **「実装担当に生成物を同期させる」「実装担当に push させる」**
を禁じており、範囲の検査（`--scope`）は範囲外を触ったコミットを含む項目を失敗にする。
**整形の修正はオーケストレーターの仕事になる。**

オーケストレーターには `--sync-command` があるが、これは「生成物を同期するコマンド」で、
**push の直前に 1 度しか走らない**。整形は各コミットの時点で満たしているべきもので、
性質が違う。

### 直さないと何が起きるか

**リファクタリングが成功したまま、Pull Request が落ちた状態で終わる。** 収束の報告は正常に出るため、
継続的統合を見るまで気づかない。整形の基準を持つリポジトリでは毎回起きる。

**継続的統合の結果を最終ゲートで見る起動では、落ちたまま終わらない。** `development-workflow` の
工程として起動し（`--workflow-step`）、`--ci-check` に検査の名前を渡すと、最終ゲートは手元の
テストの代わりにその検査の結論で判定する（`scripts/refactor_lib/commands/gate.py:158` が `gate_ci.py` の `ci_gate`（:50）と `_local_gate`（:456）を選ぶ）。
落ちれば最終ゲート修正へ回る（終了コード 2）。`--ci-check` は
ndf v10.6.0 から `SKILL.md` にある。

この気づかない状態が残るのは、次の 2 つの起動である。

| 起動 | 最終ゲートが見るもの |
| --- | --- |
| 単独の起動（`--workflow-step` 無し） | 最終ゲートは `cross-review` で、継続的統合の結論を見ない（`gate.py:75` の `standalone`） |
| `--workflow-step` だけで `--ci-check` も宣言の `test.ci.check` も無し | 手元で全体テストをもう 1 度実行する（`gate.py:456` の `_local_gate`）。宣言がテストだけなら整形の違反は通る |

`--ci-check` で見るのは名前が一致した 1 つの検査だけなので、整形が別の名前の検査にあるリポジトリでは、
その起動でも拾えない。

with-bi ではオーケストレーターが `uv run black scripts` を実行して 1 コミットを足すことで解消したが、
**これは `cross-refactoring` の手順に無い操作**である。

### 直し方の候補

採るのは案 B の宣言の側である（上の「方針」）。案 A・C は採らなかった案として残す。

### 案 A: `--baseline-test` の説明を変える

引数の表の `--baseline-test` と `--round-test` の説明を「検証」とし、**リポジトリが継続的統合で
実行する検査（テスト・整形・静的解析）をすべて含める**ことを書く。例も
`"pytest -q && ruff check ."` のような形にする。

最も小さい変更だが、**渡す側が忘れると同じことが起きる**。

### 案 B: `init` が継続的統合の検査と突き合わせる

`init` の時点で、リポジトリの継続的統合が実行するコマンドと宣言の `test`（または `--baseline-test`）を比べ、
含まれていない検査があれば知らせる。`cross-review` の `init` が認証を確かめているのと
同じ位置づけになる。

**リポジトリごとに検証コマンドの宣言が要る**（#357 が扱っている領域と重なる）。

**その宣言は `.ndf/project.json` の `test` で受けられるかを先に確かめる。** `test.suites` に
suite の種別（テスト / 整形・静的解析）を足すか、整形・静的解析を別の suite として並べ、継続的統合で見る
ものは `test.ci.check` で名指しする形である。`init` はその宣言と継続的統合の検査を突き合わせる。

**宣言の側（suite の種別）は #1483 で入った。** 残るのは、(1) 各リポジトリの宣言へ整形の suite を入れること
（このリポジトリでは `ruff format --check {paths}` を suite にするか、`check-lint.sh` に範囲を受けさせる）、
(2) `init` が継続的統合の検査と宣言を突き合わせること、の 2 つである。

### 案 C: オーケストレーターの後処理として整形を通す

`--sync-command` と同じ位置（push の直前）で整形を実行し、差分があればオーケストレーターのコミットとして
積む。**各コミットの時点では違反が残る**ため、コミット単位の取り消しをしたときに違反が
戻りうる。

案 A が最も小さく、案 B が確実である。

### 振り返りからの追記（2026-09-29）

マイルストーン 26 では、リファクタリングの項目が行数の上限・同名関数の検査（`scripts/tests/test_check_script_structure.py`）と ruff format を破り、最終ゲートか push の時点で初めて落ちた（#1399（CLOSED）に症状として記録）。構造の検査は全体テストの中でしか走らず、項目ごとの範囲テストでは走らない。**直すときは、項目ごとの検証で `check-script-structure.py` と `scripts/check-lint.sh` も走らせることを受け入れ条件に含める。** `check-script-structure.py` は #1668 で範囲テストに入り、満たした。残るのは `check-lint.sh` の整形・静的解析である。

### 着手の条件

無し（#1483 は完了、10.17.46）。

### 由来

`takemi-ohama/with-bi` マイルストーン10 の振り返り
（https://github.com/takemi-ohama/with-bi/pull/98#issuecomment-5567447600）

### 関連

- 親 issue: #1483（修正レイヤー: テストの戦略の宣言と解釈。`scripts/lib/test_strategy.py` と `.ndf/project.json` の `test`）
- #1691 言語別の整形・静的解析の規約を NDF が持ち、規約の無いリポジトリへ移植する（この課題から切り出した）
- #1507 cross-review の側の同じ形
- #1434（CLOSED） 範囲テストの雛形が静的解析（shellcheck）だと判定が壊れる — 静的解析を宣言に入れるとき、範囲テストの雛形へそのまま渡すと同じ判定の誤りを踏む。種別の宣言で両方を分ける
- #853（CLOSED） `detect-test-runner` — そのリポジトリの検証コマンドを返す部品。案 B の宣言の代わりの候補
- #357（CLOSED） 対象リポジトリの検証コマンドを宣言として受け取る手段が無い — 案 B はこの宣言に依存する
- #443 リファクタリングの方法論を見直す — 「テストを変えない」の位置づけを定める — テストを
  **どこまで変えてよいか**の規約。この課題はテストの宣言が**何を含むか**で、関心が異なる

### 再発（2026-09-30、スプリント m1523・PR 1564）

リファクタリングの修正の段（変更起因の失敗を直したコミット）が `ruff format` を通さずにコミットし、`git push origin HEAD:sprint/m1523` が push 前の検査（`check-lint: 違反あり: ruff format --check`）で落ちて、refactor が exit=4（採用 0・未確認 11）で止まった。作業ディレクトリ `/tmp/ndf-worktrees/devbasex--ai-plugins/rf1564/work` で `scripts/check-lint.sh --fix` を通して push し、`run 5-check.json --from review` で再開した。cross-review の側の同じ形は #1507。

### 再発（2026-10-02、スプリント m1340・PR #1634）

cross-refactoring が取り消しの後に `git push origin HEAD:sprint/m1340` したところ `ruff format --check` で拒否され、refactor のステップが exit 1 になった。記録が残らない件は #1648 にある。
