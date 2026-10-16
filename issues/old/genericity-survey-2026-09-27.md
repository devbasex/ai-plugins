# NDF の汎用性の調査（2026-09-27）

NDF の中で ai-plugins の形を既定として埋め込み、ほかのプロジェクトで止まる・誤動作する箇所を洗い出した。
サンプルは 4 つ（利用者の指定）。目的は、この 4 つで NDF が動くようにすることである。

## 例: carmo-system-console で `/ndf:development-workflow` を回すと、どこで止まるか

carmo-system-console は Laravel ＋ PHPUnit のアプリで、`main` へのマージがそのまま本番（CodePipeline → ECS）に届く。
`develop` は無く、テストは `docker compose exec app php artisan test` で走り、全体を直列で走らせると約 64 分かかる
（CI の JUnit 24 本の合計 3,827 秒。CI は 24 本に分けて壁時計 6.5 分）。

1. **手順 0**: `.ndf/worktree.json` は `.gitignore` の対象で、中身は `version` だけ（`base_branch` が無い）。
   ほかの宣言（`supervise.json` など）を作る手順は無い → #1333
2. **worktree**: `/tmp/ndf-worktrees` や `.worktrees/` に `vendor/` が無いので、pre-commit の Pint が
   `./vendor/bin/pint: No such file` で落ちる。記録では `--no-verify` のコミットが 6 件、`rm vendor && cp -a` の手作業が繰り返された → G4
3. **実装・検査のプラン**: 雛形は全体テストを 1800 秒で打ち切る（`supervise_lib/templates.py:112`）。宣言で変えられない → G2
4. **構造改善**: `new check` は `--round-test` を渡さないので、phpunit では `init` で必ず止まる（`templates.py:161`）。
   `--round-test` を足しても、着手前のテストの上限が予算の 10%（30 分なら 180 秒、`timeline.py:24`）で止まる。
   記録では 2 回起動して、2 回とも本体を回す前に `refactoring` へ退避した → #1334・G2
5. **危険フラグ**: `use App\Services\UserService;` に D3 が当たらず（`danger.py:74`）、
   `docker compose exec -T app ...` の `app` を範囲の起点と読んで D4 も立たない（`scope.py:147`）。全体テストが最終ゲートまで走らない → G2
6. **マージ**: `gh pr merge --admin --merge` の固定（`merged-steps.py:252`）。必須レビュー 1 件の ruleset を管理者権限で迂回する → G5
7. **配布**: `release-steps.py` は `develop` → `main`・`ndf--v<版>` のタグ・`CHANGELOG.md`・`plugin.json` を固定している。
   このリポジトリは版数を持たず、**`main` へのマージが本番の配布そのもの**である。ゲート 2 が「main へのマージ」に掛からない → G3

## サンプルの形

| | carmo-system-console | carmo-contractors-app | project-trygroup-prd | with-ai-dev |
| --- | --- | --- | --- | --- |
| 言語 | PHP 8.3・Laravel 10（Vite） | TypeScript（SAM の Lambda ＋ React 18） | Python 3.12 FastAPI ×3 ＋ Next.js 15 ×2 ＋ Terraform（モノレポ） | Python 3.13 の ML 実験と文書（submodule 6） |
| テスト | PHPUnit 13,178 件、コンテナ越し、MySQL 3 DB | api は Jest（実 DB と起動済み API が要る。CI で走らない）、web は Vitest | `./scripts/run-python-tests.sh`（poetry、サービスごと、`python -m pytest`）。CI 88 秒 | 1 ファイルだけ。CI 無し |
| 全体テストの時間 | 直列 64 分（CI 6.5 分） | Vitest 36 秒 | 約 50 秒（3 サービス） | — |
| 検査 | Pint・Larastan（差分だけ） | ESLint・Prettier・tsc | CI では無し | black |
| 起点のブランチ | `main`（`develop` 無し） | `main`（`develop` は 2024 年から放置） | `develop`（stg）→ `main`（prd） | `main` |
| 環境ブランチ | `qa/*` 7 本（毎週 main から作り直す）。PR の 38% が `qa/epsilon` 宛て | `alpha`・`epsilon`・`delta` 宛てが 55% | `qa/beta`・`qa/gamma` | 無し |
| 配布 | main へのマージで本番へ自動デプロイ | フロントは Amplify が自動、API は `sam deploy` を手で | develop で stg、main で prd へ自動デプロイ | 手動（Lambda・SageMaker） |
| 版数 | 無し（タグは 2024 年で止まっている） | 運用していない | 無し | 雛形の `0.1.0` |
| 課題の正本 | Redmine（GitHub Issues も併用） | 外部のチケット（Notion から PR の題を引く） | `issues/*.md`（番号は GitHub Issues と別） | `issues/*.md`（GitHub Issues は 0 件） |
| マージの保護 | ruleset（必須チェック 3・レビュー 1） | ruleset ＋ 旧来の保護（enforce_admins） | 無し | 無し |
| `.ndf/` | `worktree.json`（gitignore、`base_branch` 無し） | 無し | 無し | 無し |
| 指示書 | `AGENTS.md` → `docs/development/ai-agent-shared.md` | AGENTS.md・CLAUDE.md が無い | CLAUDE.md 8 行 | `CLAUDE.ndf.md`（NDF v2.1.0 の古い案内）を毎回読み込む |

**NDF の既定（pytest を uv で・`develop` 宛て・`plugin.json` の版数・`claude plugin validate`）と合うのは、
trygroup の `develop` 宛てだけである。**

## 見つけたもの

NDF 側の洗い出しは 51 件（supervise・release 系 28、cross 系・hook 23）、過去の会話の記録の事例は 23 種類。
同じ原因をまとめて 8 つの主題にした。番号の付いた課題は既存のもの。

| 主題 | 何が起きるか（サンプル） | 主な箇所 | 行き先 |
| --- | --- | --- | --- |
| **G1 宣言の作成** | 宣言を作る手順が `worktree.json` にしか無い。宣言が無いと ai-plugins の形で動く（後片付けの起点が `develop`、手順の例が `origin/develop`） | `merged-steps.py:195`、`workflow-modes.md:202`、`cross-refactoring/SKILL.md:121`、`supervise_lib/decl.py`（schema 無し） | #1333 に追記 |
| **G2 テストの戦略と時間** | phpunit で `new check` が止まる。雛形の打ち切りが 900 / 1800 / 3600 秒の固定。着手前のテストの上限が予算の 10%。落ちたテストの見分けが pytest だけ（`--lf`・要約の解析）。D3 が PHP・TS の import に当たらず、D4 がコンテナのサービス名を範囲と読む。テストを同じ場所に置く構成（`src/**/*.spec.ts`）で `--scope` が止まる。既知の失敗を抱えたままでは着手できない | `templates.py:112,161`、`paths.py:90`、`steps.py:156`、`timeline.py:24`、`budget.py:47`、`danger.py:74`、`scope.py:70,147`、`triage.py:89`、`allocation-defaults.json` | #1334 に追記 |
| **G3 配布の形** | 配布の雛形が ai-plugins 専用（`develop`/`main`・`ndf--v`・`devbasex/ai-plugins`・`plugins/<名>/.claude-plugin/plugin.json`・`CHANGELOG.md`・`X.Y.Z-dev.N`）。版数の無い Web アプリで `new mission` に `--version` が要り、`new close` が止まる。**マージが本番のデプロイになるリポジトリ（carmo・trygroup）で、ゲート 2 がそのマージに掛からない** | `release-steps.py:636-705,909`、`release-verification-steps.py:31`、`step_result.py:219,229`、`mission.py:80`、`new_args.py:170` | 新規 |
| **G4 作業ツリーの環境** | NDF の作業ツリーに `vendor/`・`node_modules/`・`.venv` が無く、pre-commit とテストが落ちる（記録 23 件）。コンテナのテストはメインディレクトリのマウントで走り、作業ツリーの変更を試さない（偽の green。推測）。Claude Code の `EnterWorktree` の隔離が NDF のスクリプトを拒む（記録 3 件） | `refactor_lib/paths.py:23`、`prepare-worktrees.sh:98`、`worktree` Skill | 新規 |
| **G5 マージと PR の運用** | `--admin --merge` の固定。squash のリポジトリで検査のトリガーが PR を 0 本と数える。環境ブランチ（`qa/*`・`alpha`）宛ての PR を工程が扱えない。cross-review が commit status の CI を見ない | `merged-steps.py:252`、`check-trigger.py:67,271`、`review_lib/ci.py:4` | 新規 |
| **G6 言語ごとの判定** | テストのパスの目印を名前の部分一致で見るため `latest_price.py`・`contest_rules.ts` をテストと判定し、`app/Services/UserServiceTest.php` を取りこぼす。PR の分類が CamelCase の `UserPolicy.php`・`app/Jobs/`・`*.blade.php` を拾わない。指標の言語表に `.vue`・`.svelte` が無い | `pathkinds.py:12`、`review_lib/categories.py:33,64,103,173`、`codemetrics.py:66` | 新規 |
| **G7 課題の正本** | 課題の正本が Redmine・Notion・`issues/*.md` で、GitHub Issues の番号から起動する工程と番号が合わない | 工程全般 | #479 に追記 |
| **G8 cross-review の再開と hook** | 収束・異常終了の後にもう 1 ラウンド回す正規の手順が無く、state を手で書き換えた（記録 6 件）。収束の直後に振動検知が中断を出す。hook が 5〜41 秒で打ち切られる。worktree-guard の誤検知（10 件中 8 件） | `cross-review/scripts/state.py`、hook | 新規（誤検知は #734） |

**文体の規則（doc-lint）がプロジェクトの文書へ掛かる**（`supervise_lib/paths.py:31`）のと、
**`.md` の文言テストを退ける**（`refactor_lib/verify.py:437`）のも、ai-plugins の方針を他へ課している。G1 の宣言で切れるようにする。

## 汎用に作られていたもの

- `pr-steps.py:69` と `lib/worktree-branch.sh:92` は、宛先と起点を origin の HEAD → main → master の順で解く
- `merged_lib/checks.py:221` は、CI の無いリポジトリを 60 秒で見分けてマージへ進む
- `worktree-testenv.sh` は、コンテナのテスト環境を宣言の `compose_files` で組む
- `mission.py` の `manual_release_wave` は、配布の雛形が無ければ手で行うリリースのステージを置く

## 使われていないもの

4 つのサンプルの記録で、`supervise.py`・`release-steps.py`・`merged-steps.py`・`refactor.py` は 1 度も呼ばれていない。
使われているのは `/ndf:cross-review` が中心で、事例の多くはレビュー担当の CLI の出力の契約・時間切れ・認証
（約 120 件。多くは閉じた課題で直した）である。**3 層の工程をサンプルで回した実績は無い。**

## 根拠

- サンプルの形: 各リポジトリの `origin/<既定>` を `git ls-tree` / `git show` で読み、`gh` で ruleset・PR・run・JUnit を取った
- NDF の洗い出し: `plugins/ndf/` の grep と、`/tmp/ndfprobe`（Laravel 風の仮のリポジトリ）での実測
- 事例: `~/.claude/projects/-work-carmo-system-console/` ほかの会話の記録（抽出のスクリプトは `/tmp/ndfscan/`）
