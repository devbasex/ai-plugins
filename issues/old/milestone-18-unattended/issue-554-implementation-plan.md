# #554: エージェント向け指示書を適切に保つ検査を NDF から配る（実装プラン）

## 関連リンク

| 文書 | 中身 |
| --- | --- |
| [issue-554-requirements.md](issue-554-requirements.md) | 受け入れ条件 86 件 |
| [issue-554-design.md](issue-554-design.md) | 設計（処理の流れ・データ構造・入出力の契約） |
| [issue-554-design-criteria.md](issue-554-design-criteria.md) | 観点の採否・観点のデータ・型と判定の手続き |
| [issue-554-design-scope.md](issue-554-design-scope.md) | スコープと扱い（直す / 起票 / 報告）・呼び方 |
| [issue-554-design-decisions.md](issue-554-design-decisions.md) | 決定 18 件 |
| [issue-554-design-application.md](issue-554-design-application.md) | このリポジトリへの適用・テスト設計 |

## モード

`standard`（新機能の追加・複数ファイルにまたがる・配布物とこのリポジトリの両方を触る）。

## 目的と非目的

達成したい状態:

- NDF を入れた任意のリポジトリで、指示書の壊れた即時読み込みが機械で落ちる
- 版の判断を指示書へ書くリポジトリで、出た版の段落が残ったまま Pull Request を出すと落ちる
- 毎回の読み込みの量が、実行するたびに数字で出る

やらないこと:

- 新しい Skill を足さない（決定 1）
- 指示書の中身を自動で書き換えない・課題を立てない（決定 17）
- `--refresh` が観点のデータを自動で書き換えない（決定 14）
- ruleset への `instruction-files-check` の追加（利用者・進行側がマージ後に行う）

## 前提

- 前提 1: Python 3 の標準ライブラリだけで動く（`re` / `json` / `argparse` / `pathlib` /
  `subprocess` / `urllib` / `hashlib` / `datetime`）
- 前提 2: 既定の検査は通信しない。通信するのは `--refresh` だけである
- 前提 3: 即時読み込みの一致範囲は Claude Code 2.1.274 の実測（設計の「実測」）に従う

## 受け入れ条件

**受け入れ条件は [issue-554-requirements.md](issue-554-requirements.md) の AC1〜AC86 が正本である。**
ここへ写さない（写すと片方だけが古くなる）。

## 修正対象

| ファイル | 新設 / 変更 |
| --- | --- |
| `plugins/ndf/scripts/instructions-check.py` | 新設（実体） |
| `plugins/ndf/scripts/data/instruction-criteria.json` | 新設（観点・出典・調べ直した日） |
| `plugins/ndf/scripts/lib/refresh.py` | 新設（調べ直しの部品。#743 と共有） |
| `plugins/ndf/scripts/tests/test_instructions_check.py` | 新設（単体テスト） |
| `plugins/ndf/skills/release/SKILL.md` | 変更（退避の段落へ検査の呼び出しと参照への案内） |
| `plugins/ndf/skills/release/references/instruction-files.md` | 新設（宣言の書き方と判定の一覧） |
| `plugins/ndf/skills/release/schemas/instructions.schema.json` | 新設（宣言の定義） |
| `plugins/ndf/scripts/lib/README.md` | 変更（置いてあるものの表へ 1 行） |
| `.ndf/instructions.json` | 新設（このリポジトリの宣言） |
| `.github/workflows/runtime-plugin-validate.yml` | 変更（ジョブ `instruction-files-check`） |
| `CLAUDE.md` | 変更（版が決まる前の印と検査のコマンド） |
| `docs/versioning-and-distribution.md` | 変更（手順へ検査を足し、手順 4 の置き場所を直す） |
| `CHANGELOG.md` | 変更（冒頭の置き場所を直す） |

## タスク分解

分けるのは判定の単位である（レイヤーで分けない）。各タスクは「失敗するテスト → 最小実装 → 整理」で進める。

### Task 1: 走査と量の報告（F1）

- **対象:** `instructions-check.py`（`Declaration` / `Target` / `collect_files` / `main` の骨格）、
  `data/instruction-criteria.json`
- **変更内容:** 観点のデータを読み、宣言を読み（無ければ既定）、`git ls-files -z` から
  指示書を集め、本数と根の指示書ごとのバイト数を出して 0 で終わる
- **満たす受け入れ条件:** AC1〜AC5 / AC29 の本文分 / AC31 / AC33〜AC38 / AC69〜AC71

### Task 2: 即時読み込みの判定（F2 / F3）

- **対象:** `instructions-check.py`（`import_findings` / 参照の解決と推移閉包）
- **変更内容:** コードブロック・コードスパンの外の `@` を実測の一致範囲で拾い、参照先の存在・
  許可の有無・許可の陳腐化を判定する。量の集計は推移閉包で深さ 4 段まで
- **満たす受け入れ条件:** AC19〜AC30 / AC50〜AC52

### Task 3: 出た版の段落（F4）

- **対象:** `instructions-check.py`（`version_findings` / 版数の比較 / `released` の読み取り）
- **変更内容:** 変更履歴またはタグから最新の版を取り、段落の先頭と見出しの書き出しを見る。
  semver の prerelease の順序で最大値を決める
- **満たす受け入れ条件:** AC53〜AC65

### Task 4: 量の上限と指示の数（F5 / F7）

- **対象:** `instructions-check.py`（`budget_findings` / `count_findings`）
- **満たす受け入れ条件:** AC32 / AC66

### Task 5: スコープと扱い（F9）

- **対象:** `instructions-check.py`（`Source` / `action_of` / `--scope`）
- **変更内容:** `--scope user` / `--scope plugins` で宣言の `scopes` の位置を走査し、
  スクリプトの実体が `--root` の下にあるかで扱いを分ける
- **満たす受け入れ条件:** AC6〜AC18

### Task 6: 調べ直し（F8）

- **対象:** `plugins/ndf/scripts/lib/refresh.py`、`instructions-check.py`（`--refresh`）
- **変更内容:** 出典の取得・指紋の比較・一覧の提示・待ちの扱い。書き換えない
- **満たす受け入れ条件:** AC39〜AC47

### Task 7: 宣言の検証（構造・範囲）

- **対象:** `instructions-check.py`（`Declaration.load`）
- **満たす受け入れ条件:** AC48 / AC67 / AC68

### Task 8: 配布（F6）

- **対象:** `release/SKILL.md`、`release/references/instruction-files.md`、
  `release/schemas/instructions.schema.json`、`lib/README.md`
- **満たす受け入れ条件:** AC49 / AC72〜AC76

### Task 9: このリポジトリへの適用

- **対象:** `.ndf/instructions.json`、`.github/workflows/runtime-plugin-validate.yml`、
  `CLAUDE.md`、`docs/versioning-and-distribution.md`、`CHANGELOG.md`
- **満たす受け入れ条件:** AC77〜AC82 / AC84
- **AC83 は実装の差分に入らない**（設計の「このリポジトリへの適用」の 6。ruleset へ足すのと
  同じ時点で進行側が直す。直す 3 か所と文言は Pull Request の本文へ残す）

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 配布物へスクリプト 1 本・観点のデータ 1 本・参照 1 本・定義 1 本。Skill の数は変わらない |
| 既存の振る舞い | `release` の退避の手順に検査の実行が 1 つ増える。宣言の無いリポジトリでの振る舞いは変わらない |
| このリポジトリ | 継続的統合に検査が 1 つ増える |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| このリポジトリ自身へ掛けると落ちる（`CLAUDE.md` の `**@AGENTS.md**`・出た版の段落） | 宣言で `imports` へ理由とともに足し、残った出た版の段落は退避先へ移す。落ちる状態を残さない |
| `release/SKILL.md` を #561 #623 の実装も触る | 触るのは「3. 版と説明文書を更新する」の退避の段落だけ。競合したら後からマージする側が解く |
| `instructions-check.py` が 1 ファイルで肥大する | 判定ごとに関数を分け、`lib/` へ出すのは調べ直しの部品だけに絞る。構造は `cross-refactoring` で見る |
| 行数の検査（501 行以上）に新設の文書が掛かる | 参照は 500 行以内に収める |

## 切り戻し手順

配布物の新設と、このリポジトリの宣言・ジョブの追加だけである。Pull Request を revert すれば
元へ戻る。データの移行は無い。

## 完了の定義

- [ ] AC1〜AC86 のうち、実装の差分に入るものをすべて満たす（AC83 は除く）
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` の失敗が増えない
- [ ] 既存の検査 7 本が終了コード 0
- [ ] `bash scripts/build-runtime-plugins.sh --check` / `bash scripts/validate-runtime-plugins.sh` が 0
- [ ] このリポジトリの根で `instructions-check.py` が終了コード 0
