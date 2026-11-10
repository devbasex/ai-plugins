# 差分の中身で検査の工程を選ぶ: 文書だけの変更に cross-refactoring と全体テストを掛けない

## 概要

NDF の検査のプランと、このリポジトリの CI は、Pull Request の差分を**文書のファイル**と**コードのファイル**に分け、
cross-refactoring と全体テストをコードのファイルにだけ掛ける。分け方は NDF の部品 1 つ（`plugins/ndf/scripts/lib/diff_kinds.py`）が持ち、
検査のプランと CI が同じ部品を読む。

例: 用語集だけを変えた Pull Request は、cross-refactoring を飛ばし、全体テストの代わりに用語集を読むテストだけを走らせる。
CI では全体の pytest のジョブ（`pytest (0/2)`・`pytest (1/2)`）を省き、`pytest (文書)` のジョブがそのテストを走らせ、
必須のチェック `pytest` は成功で終わる。Skill の本文を変えた Pull Request では、さらに振る舞いの確認を通す。

## 用語

語の定義は用語集（ [docs/glossary.md](../glossary.md) ）が正である。この文書で使う語は次のとおり。

| 用語 | この文書での使い方 |
| --- | --- |
| 文書のファイル | 差分のうち、文章として読まれるファイル。既定は Markdown と、用語集の設定が指す用語集のファイル |
| コードのファイル | 差分のうち文書のファイルでないもの。スクリプト・設定・テスト・ワークフローを含む |
| テストが読む文書 | 文書のファイルのうち、宣言でテストとの対応を持つもの |
| 振る舞いを決める文書 | 文書のファイルのうち、AI の振る舞いを決めるもの（Skill の本文・手順書・エージェント定義） |
| 振る舞いの確認 | 振る舞いを決める文書を変えたとき、AI がその手順どおりに動くかを `claude -p` で実際に起動して確かめること |

## 背景

| 決めたこと | 理由 |
| --- | --- |
| 分け方を NDF の部品 1 つにし、呼び出し元は差分の一覧を自分の手段（REST・git）で取って渡す。CI は同じコミットの部品を import する | 検査のプランと CI で分け方を 2 か所に持たないため。GitHub のアクションでパスを判定する形は、手元で動く検査のプランから呼べない |
| テストが読む文書とテストの対応は、プロジェクトの宣言で持つ | テストの側に印を書かせる形はランナーごとに書き方が違う。実行から依存を求めるツール（pytest-testmon など）は、データとして読むファイルを追わない |
| 文書のファイルの既定は、Markdown と用語集の設定が指すファイルにする | NDF の既定に特定のリポジトリのパスを持たないため。用語集の場所は用語集の設定が既に持つ |
| cross-refactoring を飛ばす判定は、既存のリファクタリングの要否の判定（`assess`）に置く | 同じ判定を 2 か所に分けないため |
| 全体テストの選び方は `test-run.py whole` の中で決める | 検査のプラン 2 つと実装のプランが同じコマンドで呼ぶため、プランのテンプレートを変えずに 3 つが同じに振る舞う |
| 振る舞いの確認は worker が `claude -p` で 1 対象 1 回起動し、Pull Request のコメントに記録する | 何を確かめるかは差分の中身で決まり、スクリプトでは決められない。対象の抽出・記録の形・記録の確認はスクリプトが担う |
| 新しいモードを足さず、`light` と `documentation` の定義も変えない | 文書だけの差分に残る工程（設計・cross-review・完了判定・文書の検査）は、どれも文書の変更に意味を持つ。`documentation` はリポジトリの外の読み手へ渡す文書の工程を持つ |
| runtime smoke では、振る舞いを決める文書をコードとして扱う | Skill の本文の frontmatter はランタイムの読み込みを変え、読み込めることは smoke だけが確かめる |

## 仕様

### 採る工程

| 差分の形 | cross-refactoring | 全体テスト（検査のプラン） | CI の pytest | 振る舞いの確認 |
| --- | --- | --- | --- | --- |
| 文書のファイルだけ（テストが読む文書・振る舞いを決める文書を含まない） | 飛ばす | 飛ばす | 全体のジョブを省く | 通らない |
| 文書のファイルだけで、テストが読む文書を含む | 飛ばす | 対応するテストだけ | `pytest (文書)` のジョブで対応するテストだけ | 振る舞いを決める文書を含めば通る |
| 文書のファイルだけで、振る舞いを決める文書を含む | 飛ばす | 上の 2 行のどちらか | 上の 2 行のどちらか | 通る |
| コードと文書が混ざる | 通す（範囲はコードのファイルとテストの置き場所だけ） | 全体 | 全体 | 振る舞いを決める文書を含めば通る |
| コードのファイルだけ | 通す | 全体 | 全体 | 通らない |
| 差分の一覧が取れない・宣言が読めない | 通す | 全体 | 全体 | 通らない |

cross-review・完了判定・静的解析と文書の検査は、どの差分でも通す。版数と説明だけの差分を省く CI の判定（版上げと
本番の Pull Request）は、この表の前に効く。工程表の決まりは `development-workflow` の `SKILL.md` の「差分の中身で選ぶ工程」にある。

### 差分の分類

| 集約 | 持ち主 | 値 |
| --- | --- | --- |
| 差分の分類 | `lib/diff_kinds.py`。ほかの構成要素は結果を読むだけ | 文書のファイル・振る舞いを決める文書・テストが読む文書・コードのファイル・走らせるテスト・分類できなかった理由 |
| 文書の分け方の宣言 | 利用者（`.ndf/project.json` の `docs` を手で書く）。NDF は読むだけ | パターンの並びと、テストが読む文書の対応の行 |

`load_docs_decl` は宣言を読んで既定で補い、`classify_files` はファイルも git も読まない純粋な関数として差分を分ける。
`classify_root` は宣言を読んで分け、宣言が読めなければ理由つきで全部をコードにする。`git_changed` は
`git diff --name-only --no-renames <範囲>` で差分の一覧（消したファイルを含む）を取り、取れなければ `None` を返す。

常に成り立つ条件:

| 条件 | 破れたときの扱い |
| --- | --- |
| 差分の各ファイルは、文書のファイルかコードのファイルのどちらか 1 つにだけ入る | コードのファイルは「文書のファイルでないもの」として求めるため、両方に入ることはない |
| 差分の一覧が取れない・宣言が読めないときは「文書だけ」と判定しない | 全部をコードとして扱い、`unknown` に理由を書く。今までと同じ工程を通る |
| 振る舞いを決める文書とテストが読む文書は、文書のファイルの部分集合である | 文書のパターンに当たらないファイルは、振る舞いのパターンに当たってもコードのファイルになる |
| 走らせるテストは、差分にあるテストが読む文書に対応するテストの和である | 差分に無い文書の対応からは入らない |
| 宣言が無い・項目が欠けたときは NDF の既定で補い、特定のリポジトリのパスを既定に持たない | — |

`docs_only()` は、`unknown` が空で、コードのファイルが無く、文書のファイルが 1 件以上のときだけ真になる。

### 検査のプランの各ステップ

| ステップ | 振る舞い |
| --- | --- |
| リファクタリングの要否の判定（cross-refactoring の `assess`） | 差分（`<起点>...HEAD`）が文書だけなら、行数を数えずに `判定: 飛ばしてよい` と `理由: 差分が文書のファイルだけ（文書 N ファイル）` を出して終了コード 3 で終える。宣言が読めなければ本番コードの行数の判定へ進む。終了コードの意味（0 = 通す / 3 = 飛ばしてよい / 2 = 判定できない）は変わらない |
| 範囲の組み立て（`refactor-scope.py`・`check-trigger.py` の `scope`） | 範囲から文書のファイルを外し、テストの置き場所の探索もコードのファイルからだけ行う。宣言が読めなければ全部を入れる |
| 全体テスト（`test-run.py whole`） | 起点との merge-base から HEAD までの差分を分類する。文書だけで走らせるテストが無ければ test の suite を走らせず、`全体テストを飛ばした（差分が文書のファイルだけ: N ファイル）` を出す。走らせるテストがあれば、範囲テストのコマンドのテンプレート（`scope_command`）でそのテストだけを手元で走らせ、落ちれば失敗を返す。静的解析の suite はどの差分でも走らせる。`items` に `{"diff_kinds": <分類>}` を載せる |
| 振る舞いの確認 | コードレビューの後に `behavior-targets`・`behavior`・`behavior-verify` の 3 ステップを置く（下） |

### 振る舞いの確認

| ステップ | 種類 | 内容 | 遷移 |
| --- | --- | --- | --- |
| `behavior-targets` | run | `behavior-check.py targets --pr {pr}` | 3 → `base-sync`、0 → `behavior`、それ以外は止まる |
| `behavior` | work | worker が `targets` の結果の `record.procedure` どおりに確かめ、`post` で記録する | → `behavior-verify` |
| `behavior-verify` | run | `behavior-check.py verify --pr {pr}` | 0 → `base-sync`、それ以外は止まる |

`behavior-check.py` のサブコマンド:

| サブコマンド | 中身 | 終了コード |
| --- | --- | --- |
| `targets --pr N [--root DIR]` | 差分（消したファイルを除く）の振る舞いを決める文書を、Skill（`.../skills/<名前>`）かエージェント定義（ファイル）の単位にまとめて出す。最後の行の `record` に PR の番号・head・`post` のコマンド・worker の手順を載せる | 0 = 対象あり / 3 = 対象なし / 2 = 差分か head を取れない |
| `post --pr N --file <結果の JSON>` | 結果を、印 `<!-- ndf:behavior-check head=<sha> -->` 付きの PR のコメント 1 件にする | 0 / 2 = 結果の形が違う・投稿できない |
| `verify --pr N` | 今の head の印を持つ最後の記録が、今の対象をすべて「通った」で覆うかを見る | 0 = 覆う / 1 = 足りない・落ちた・確かめていない・head の記録が無い / 2 = 読めない |

結果の JSON は worker が書く。

```json
{"head": "<sha>", "checks": [{"target": "plugins/ndf/skills/design", "prompt": "...",
  "command": "claude -p --plugin-dir plugins/ndf --output-format stream-json --verbose --no-session-persistence --max-budget-usd 1 '...'",
  "observed": "手順 5 の design-survey.py check を打った", "verdict": "通った"}]}
```

`verdict` は `通った` / `落ちた` / `確かめていない` のどれかである。worker は対象ごとに差分で変わった指示を 1 つ選び、
worktree の一時コピーで 1 回だけ起動し（1 回の費用の上限 1 ドル）、出力のツールの呼び出しと応答から判定する。
確かめるのは 6 件まで（`MAX_TARGETS`）で、超えた分は「確かめていない」とし、`verify` が落として人へ戻す。worker は文書を直さない。
記録は head のコミットの印で結ぶため、確かめた後に文書を変えると確かめ直しになる。

## データ・設定

`.ndf/project.json` の `docs`（省略可能）:

```json
{
  "docs": {
    "files": ["**/*.md", "docs/metrics/ndf-token-usage/*.json"],
    "behavior": ["plugins/*/skills/**/*.md", "plugins/*/agents/*.md"],
    "tested": [
      {"paths": ["docs/glossary.md", "docs/glossary/**"], "tests": ["plugins/ndf/scripts/tests/test_glossary.py"]}
    ]
  }
}
```

| 項目 | 形 | 省いたときの既定 |
| --- | --- | --- |
| `files` | wildmatch のパターンの並び（`lib/pathmatch.py` の書き方） | `**/*.md` と、用語集の設定（`.ndf/glossary.json`）の `source`・`document`。用語集の設定が無い・読めなければ `**/*.md` だけ |
| `behavior` | 同上 | `**/skills/**/*.md`・`**/agents/*.md` |
| `tested` | `paths`（パターン）と `tests`（テストのファイルかディレクトリ）だけを持つ行の並び | 空 |

項目を書くと既定を置き換える（足すのではない）。既定に戻すには項目を消す。`{"unknown": ...}` だけの値は宣言が無いのと
同じに扱う。知らない項目・型の違い・空の並びは「読めない」として扱い、全体をコードの差分とみなす。型は
`plugins/ndf/scripts/project_lib/model.py` と `development-workflow` の `schemas/project.schema.json` が定める。

このリポジトリの宣言は `files` に `docs/glossary/glossary.json` と `docs/metrics/ndf-token-usage/*.json` を足し、
`tested` にテストが読む文書とそのテストの対応を持つ。

## 外部連携

### CI の判定（`scripts/ci-heavy-skip.py`、このリポジトリだけ）

Pull Request のときだけ判定し、push では常に全体を回す。差分が文書だけなら `skip=true` を出し、テストが読む文書が
あれば対応するテストを `docs_tests` に空白区切りで出す。出力は標準出力と `GITHUB_OUTPUT` へ書く。

```text
skip=true
reason=差分が文書のファイルだけ（テストが読む文書 2 ファイル）
docs_tests=plugins/ndf/scripts/tests/test_glossary.py plugins/ndf/scripts/tests/test_doc_lint.py
```

`--workflow runtime-plugin-smoke.yml` のときは、振る舞いを決める文書をコードのファイルとして扱う。差分の一覧か
宣言が読めなければ省かない。

### CI のワークフロー（`.github/workflows/pytest.yml`）

| ジョブ | 名前 | 条件 |
| --- | --- | --- |
| `ci-scope` | `pytest-scope` | Pull Request で判定を走らせる |
| `pytest-shard` | `pytest (0/2)`・`pytest (1/2)` | `skip != 'true'` |
| `pytest-docs` | `pytest (文書)` | `skip == 'true'` かつ `docs_tests != ''`。対応するテストを `-n auto` で走らせる |
| `pytest` | `pytest`（必須のチェック） | 分けたジョブが成功、または `skip == 'true'` で分けたジョブが skipped かつ（`docs_tests` があれば `pytest-docs` が成功、無ければ skipped） |

必須のチェックは名前の結果を返さなければマージを塞ぐ。ワークフローを Pull Request で常に起動し、集約のジョブ `pytest`
が結果を返す形を保つ。

## エラー処理

- 差分の一覧が取れない・宣言が読めないときは、`assess` は行数の判定へ、`whole` は全体テストへ、CI の判定は `skip=false` へ倒れる
- `behavior-check.py` は GitHub か結果のファイルを読めないとき終了コード 2 を返す。head の記録が無ければ `verify` は 1 を返す
- 分類の結果の形を変えるときは、同じ Pull Request で CI の判定（`scripts/ci-heavy-skip.py`）とそのテストを直す

## 運用

- **宣言の漏れの検査**: `scripts/tests/test_docs_decl.py` は、テストのソースが文書のパス（`docs/a.md` の形か `"docs" / "a.md"` の形）を書いているのに、宣言の `tested` に対応を持たないテストのファイルを落とす。根の直下のファイルと、パスを組み立てて読むテストは見つけられない。漏れたテストは、ベースブランチへの push で回る全体の pytest で見つかる
- 振る舞いの確認は確率的で、「通った」は指示が効く可能性を示すだけである。「落ちた」「確かめていない」は人が判断する
- 実装のプランの範囲テスト（`test-limited`）は宣言のテストの置き場所を走らせ、文書だけの差分でも走る
- ビジネス文書の `documentation` モードの工程は変わらない

## テスト観点

- 文書だけの差分で、`assess` が終了コード 3 と理由「差分が文書のファイルだけ」を出すこと
- 文書だけの差分で、`whole` が test の suite を起動せず、飛ばした理由を出すこと。静的解析の suite は走ること
- 混ざった差分で、`refactor-scope.py` と `check-trigger.py scope` の出力に文書のファイルとそのディレクトリが入らないこと
- テストが読む文書だけの差分で、`whole` が宣言の対応のテストだけを走らせ、落ちれば失敗を返すこと
- 差分の一覧が取れない・宣言が壊れたとき、`assess`・`whole`・`ci-heavy-skip.py` が通す・全体・`skip=false` になること
- 用語集を変えた文書だけの差分で、`ci-heavy-skip.py` が `skip=true` と `docs_tests` を出し、コードかテストを含めば `skip=false` を出すこと
- 集約のジョブの条件が、ジョブの結果の組（成功・skipped・失敗）ごとに期待どおりになり、`pytest-docs` の失敗で成功を返さないこと
- `targets` が振る舞いを決める文書だけを対象にし、`verify` が古い head の記録・落ちた対象・足りない対象で 1 を返すこと
- 検査のプランのテンプレートが振る舞いの確認の 3 ステップを持ち、`skip_to` が `base-sync` を指すこと
- 宣言の無いプロジェクトで、既定が `**/*.md` と用語集の設定のファイルになること
- 分類の結果で、どのファイルも文書とコードの両方に入らず、文書のパターンに当たらないファイルがコードに入り、差分に無い文書の対応のテストが走らせるテストに入らないこと
- 宣言からテストを 1 つ消すと、宣言の漏れの検査が落ちること

## 関連リンク

- 要求: https://github.com/devbasex/ai-plugins/issues/2925 （CI の受け入れ内容は https://github.com/devbasex/ai-plugins/issues/2907 ）
- 工程表: `plugins/ndf/skills/development-workflow/SKILL.md` の「差分の中身で選ぶ工程」と `references/stage-notes.md`
- 必須のチェックの扱い: https://docs.github.com/en/enterprise-cloud@latest/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
- コード: `plugins/ndf/scripts/lib/diff_kinds.py`・`plugins/ndf/scripts/behavior-check.py`・`plugins/ndf/scripts/test-run.py`・`plugins/ndf/scripts/refactor-scope.py`・`plugins/ndf/scripts/check-trigger.py`・`plugins/ndf/scripts/supervise_lib/paths.py`・`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/assess.py`・`scripts/ci-heavy-skip.py`・`.github/workflows/pytest.yml`・`.github/workflows/runtime-plugin-smoke.yml`
