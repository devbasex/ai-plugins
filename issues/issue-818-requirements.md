# #818: Claude Code と Codex で言語サーバ（定義・参照・診断）を使えるようにする — 要求と受け入れ条件

設計は [issue-818-design.md](issue-818-design.md)、決定の理由は
[issue-818-design-decisions.md](issue-818-design-decisions.md)、テスト設計は
[issue-818-design-tests.md](issue-818-design-tests.md) にある。この文書は「何を満たすか」だけを扱う。

マイルストーンは「17 トークン消費の削減」。版は配布の工程が決める（`release`）。

## 例: `monitor.py` の `_safe_size` を変える作業を、変えた後の形で通すと

今（ndf 10.17.7-dev.1 / mcp-serena 2.0.1）の実測（#818 の §3）:

1. モデルは `Read` で `plugins/ndf/scripts/lib/monitor.py`（1,247 行・48,847 文字）を丸ごと読む
2. `Edit` で書き換える。型の食い違いは `pytest` を流すまで分からない
3. Serena は起動しているが `language_servers` が `bash` だけのため、Python のシンボルは引けない。呼んでも `No active project` で失敗する

変えた後:

1. 利用者がこのリポジトリで `/mcp-serena:language-servers` を実行する（**プロジェクトごとに実行する。** 利用者単位で 1 度打てば全部のリポジトリに効くものではない。`project.yml` を追跡すれば、同じリポジトリの他の利用者は打たなくてよい）。スクリプトが追跡対象の拡張子を数え、`python`（321 ファイル）と `bash`（71）を採る。`.js` の 4 ファイルはしきい値に届かないので外す
2. スクリプトが `.serena/project.yml` を書き、1 言語ずつ起動を検証する。Claude Code 向けには `pyright-lsp` の導入と `pyright-langserver` の有無を検査し、足りないものを知らせる
3. 以後のセッションで、モデルがコードファイルを `Read` で 3 回続けて開こうとすると、hook が 1 度だけ拒む。拒む理由の文が、シンボル単位の手順（`get_symbols_overview` → `find_symbol` → `find_referencing_symbols`）を示す
4. 編集の後、Claude Code には pyright の診断が次の手番で届く。Codex は Serena の `get_diagnostics_for_file` で取る

## 依頼（原文）

> NDF を入れた **Claude Code と Codex** で `.py` / `.php` / `.ts`（`.js`）を扱うとき、定義・参照・診断（型エラー・未解決の import）と、コードを読む量を抑えた編集ができるようにする。
>
> | ランタイム | 診断 | シンボルの把握・参照・編集 |
> | --- | --- | --- |
> | Claude Code | **plugin LSP servers** | **Serena** |
> | Codex | Serena | Serena |
>
> 役割を分け、同じ言語で機能を重複させない。

（#818 の本文の「何をしたいか」。調べ（§1〜§13）と作業項目 A〜E は #818 の本文にある）

加えて、利用者の指示が 2 つある（2026-09-24）。

> 設計文書の効果の見積もりに **減らした量 × (1 + 0.05k + 1.25m)** の式を使い、持ち場ごとの見積もりを置くこと。

> できるだけスクリプト化を進める。判断の要らない手順は scripts/ へ移し、LLM には判断だけを残す。設計の決定の記録に「どこまでスクリプトにできるか」を残す。

## 目的

- **コードを丸ごと読まずに済むようにする。** 読む量を減らすと、以後の呼び出しの読み直しとキャッシュの書き直しの分も減る（#818 §13）
- **型の食い違いを、テストを流す前に知る。** Claude Code では編集の直後に、Codex では呼べば取れる
- **導入先のリポジトリを選ばない。** 言語は導入先ごとに検出して設定する。このリポジトリ専用の設定にしない
- **動いていないことに気付けるようにする。** 言語サーバが無くても何も表示されない状態（#818 §4）を、検査で見えるようにする

## 前提

- 前提 1: Serena は PyPI の `serena-agent==1.7.0` に固定する。2026-09-24 時点の最新の正式版である（`pypi.org/pypi/serena-agent/json`）
- 前提 2: 言語サーバの本体は、Serena 側は Serena が自分で入れる（起動定義の `SERENA_HOME=.serena` により、起動したディレクトリの `.serena/language_servers/`）。Claude Code の plugin LSP servers 側は利用者が入れる
- 前提 3: Kiro CLI と agy は受け入れ条件の対象にしない。**今と同じ動き（MCP の定義を受け取る）を保つ**ことだけを求める
- 前提 4: PHP の型の食い違いの診断は、intelephense の無料版が返さない（#818 §5）。PHP の診断は「未解決のシンボル・構文」までを条件にする
- 前提 5: 効果の見積もりの k と m は #893 の集計の規則（`scripts/token-usage.py` の層・持ち場の判定）で数えた値を使う。#893 の集計スクリプトに k・m の列が入るのを待たない

## 対象範囲

含む:

- `plugins/mcp/mcp-serena/` の起動定義（版の固定・`--project-from-cwd`・ランタイム別の `--context`・`no-memories` / `no-onboarding`）
- 対応表（拡張子 → Serena の言語 → 公式 LSP プラグイン → 本体の導入の検査）と、それを読むスクリプト（言語の検出・設定の生成と起動の検証・導入の検査・hook）
- 導入と検査の Skill（`mcp-serena` プラグインに置く）
- hook: SessionStart（食い違いと未導入の通知）、PreToolUse（誘導と自動許可）。Claude Code と Codex の 2 つの定義
- このリポジトリの `.serena/project.yml` を、同じスクリプトで直すこと（`python` + `bash`、`.worktrees/**` を外す）
- NDF のコード改変系 Skill（`refactoring` / `problem-solving` / `tdd-cycle`）へ 1 行の手順を足すこと
- エージェント定義（`corder` / `qa` / `director` / `debugger`）の Serena の行だけを直すこと（古い名前空間・memory の記述）
- `mcp-serena` の README と `docs/serena-guide.md` の改訂（公式の `serena` と併用しないこと・違い・Codex への導入）
- 効果の持ち場ごとの見積もり（設計文書）と、実装後の実測

含まない:

- 起動のラッパースクリプト（導入済みの Serena を優先する）。**採らない**（決定 4）
- プラグイン名・サーバ名の短縮（決定 5）
- NDF に `lspServers` を持たせること（#818 §9 (3)）
- Kiro CLI と agy の hook・文脈の調整（前提 3）
- エージェント定義の構成の整理と、`corder.md` の Context7 の扱い（#877 #869）
- devbase のコンテナへ Serena と言語サーバを入れること（devbasex/devbase#236）
- `scripts/token-usage.py` に k・m の列を足すこと（#893）
- クラス図。作るスクリプトは型を定義しない（決定 13）

## 用語

| 用語 | 意味 |
| --- | --- |
| 対応表 | `languages.json`。言語ごとに、拡張子・Serena の言語識別子・公式 LSP プラグイン・本体のコマンド・追加の検査を持つ |
| 検出 | `git ls-files` の拡張子を数え、しきい値を超えた言語を選ぶこと |
| 採った言語 | 検出で選ばれ、起動の検証を通った言語。`.serena/project.yml` の `language_servers` に入る |
| 起動の検証 | 1 言語だけを設定した状態で `serena project health-check` を走らせ、終了コード 0 を見ること |
| 導入の検査 | Claude Code の公式 LSP プラグインと言語サーバ本体（と TypeScript の版・shellcheck）が揃っているかを見ること |
| 誘導 | PreToolUse の hook が、`grep` やコードファイルの読み込みが続いたときに 1 度だけ拒否し、シンボル単位の手順を示すこと |
| 持ち場 | conductor / supervisor / worker の層と、その役割（設計・実装・検査・仕上げ・取り込み・修正・調査・検証・集計）。#893 の語彙 |

## 受け入れ条件

### 起動の定義（作業項目 A）

- [ ] AC1: Claude Code 用の起動定義は `uvx --from serena-agent==1.7.0 serena start-mcp-server` で起動し、`git+` の URL を含まない。引数は `--context claude-code --project-from-cwd --add-mode no-memories --add-mode no-onboarding --enable-web-dashboard False` である
- [ ] AC2: Codex 用の起動定義は別のファイルで、`--context codex` を渡す。他の引数は AC1 と同じ
- [ ] AC3: Claude Code で起動した Serena のツール一覧に、`activate_project`・`onboarding`・memory の 6 ツール・`get_current_config`・`search_for_pattern` の 10 個が無い。6 ツールは `write_memory` / `read_memory` / `list_memories` / `delete_memory` / `edit_memory` / `rename_memory` である
- [ ] AC4: このリポジトリの `.serena/project.yml` は `language_servers` が `python` と `bash`、`ignored_paths` に `.worktrees/**` を持つ。検出のスクリプトの出力と食い違わない

### 検出と設定（作業項目 E）

- [ ] AC4b: 導入の Skill は**プロジェクト（リポジトリ）ごとに実行するもの**である。Skill の本文の冒頭・mcp-serena の README・SessionStart の通知の文の 3 箇所に「プロジェクトごとに実行する（利用者単位で 1 回ではない）」ことが書かれ、`configure` を打っていないリポジトリで SessionStart が「このリポジトリでは未設定。`/mcp-serena:language-servers` を打つ」と知らせる（Serena を使うリポジトリの判定は設計の「hook の振る舞い」に従う）
- [ ] AC5: 検出のスクリプトは、対応表にある言語のうち「ファイル数 10 以上かつ対応表の拡張子のファイル全体の 5% 以上」のものを採る。採らなかった言語と理由（件数・割合）を出力に残す
- [ ] AC6: 設定のスクリプトは確認を取らずに書く。`.serena/project.yml` が無ければ作り、あれば `language_servers` と `ignored_paths` と外した言語の記録（`mcp_serena_excluded`）だけを書き換え、他のキーと注釈を保つ
- [ ] AC7: 設定のスクリプトは、採る言語を 1 つずつ起動の検証にかけ、失敗した言語を設定から外す。外した言語と理由を出力と `.serena/project.yml` に残す（`--only` で名指しされなかった言語も同じ）
- [ ] AC8: 壊れた言語サーバが 1 つある状態で設定のスクリプトを走らせると、その言語だけが外れ、残りの言語で `find_symbol` と `find_referencing_symbols` が通る
- [ ] AC9: `--dry-run` では検出と差分の表示だけを行い、ファイルを 1 つも書かない
- [ ] AC10: `.gitignore` は利用者が `--gitignore` を、`.serena/.gitignore` は `--serena-gitignore` を渡したときだけ書き換える。渡さなければ `configure` 自身は触らない（`project.yml` が無いときに `serena project create` が作る Serena の既定の `.serena/.gitignore` は許す）。`--serena-gitignore` の後、導入先の `git status --porcelain .serena` に `serena_config.yml`・`logs/`・`language_servers/`・`cache/` が出ない
- [ ] AC11: 言語構成の違う 2 つ以上のリポジトリで、検出 → 設定 → 起動の検証 → 導入の検査が通る。少なくとも 1 つは Python 以外が主のもの（例: `/work/carmo-system-serverside`）
- [ ] AC12: `project.yml` が作業ツリーにある（追跡している。決定 12）とき、作業ツリー（`.worktrees/<ブランチ名>`）で起動した Serena が、作業ツリーのプロジェクトを有効にする（Claude Code は `find_symbol` の結果のパス、Codex は `get_current_config` で確かめる。Claude Code の文脈には `get_current_config` が無い）。追跡しないときの振る舞いは U4 の解消を待つ

### 導入の検査（作業項目 C）

- [ ] AC13: 導入の検査のスクリプトは、採った言語ごとに、公式 LSP プラグインの導入（`~/.claude/plugins/installed_plugins.json`）と本体のコマンドの有無を報告する。欠けがあれば終了コード 1（`installed_plugins.json` が無いときを含む）、揃っていれば 0、読めなければ 2（`installed_plugins.json` が JSON として読めないときを含む）
- [ ] AC14: TypeScript を採ったリポジトリで、`typescript-language-server` が解決する `typescript` の版が 5 でないとき、検査が知らせる
- [ ] AC15: Bash を採ったリポジトリで `shellcheck` が無いとき、検査が知らせる
- [ ] AC16: NDF と `mcp-serena` のどちらも `lspServers` を宣言しない

### hook（作業項目 B・D・E）

- [ ] AC17: SessionStart の hook は、検出の結果と `.serena/project.yml` が食い違うとき（設定のスクリプトが外した言語として記録したものは食い違いに数えない）、または導入の検査に欠けがあるときだけ 1 行以上を出す。揃っているときは何も出さない
- [ ] AC18: SessionStart の hook は、`.serena/project.yml` が無いか、設定のスクリプトが書いた印（`mcp_serena_excluded`）の無いリポジトリ（Serena が自動で作った設定を含む）では何も出さない
- [ ] AC19: PreToolUse の hook は、`.serena/project.yml` の採った言語の拡張子のファイルについてだけ数える。`.md` や `.json` の読み込みでは拒否しない
- [ ] AC20: PreToolUse の hook は、`grep` 3 回・コードファイルの読み込み 3 回・混在 4 回のどれかに達したとき 1 度だけ拒否し、数を戻す。拒否から 120 秒は拒否しない
- [ ] AC21: PreToolUse の hook は、Claude Code の許可のモードが `acceptEdits` か `auto` のとき、Serena のツールの呼び出しを許可する。それ以外のモードでは何も返さない
- [ ] AC22: Codex の hook 定義は Claude Code と別のファイルで、PreToolUse の対象はシェルの呼び出し（`Bash` / `shell` など）である
- [ ] AC23: hook の 1 回の所要は、1 万ファイルのリポジトリで SessionStart 1 秒以内・PreToolUse 0.2 秒以内である

### ランタイムごとの実測（受け入れ）

- [ ] AC24: Claude Code: Python / TypeScript（5 に固定）/ PHP のそれぞれで、編集の後に `<new-diagnostics>` が届く（PHP は未解決のシンボルか構文の誤りで確かめる）
- [ ] AC25: Codex: `codex exec` から、Python / TypeScript / PHP のそれぞれで `find_referencing_symbols` と `get_diagnostics_for_file` が結果を返す。Skill が Codex から `serena-lsp.py` に届くことも確かめる。あわせて、Codex の hook が実ランタイムで効くこと（SessionStart の通知がモデルに届くこと、採った言語のファイルをシェルで 3 回読むと 3 回目が拒否されること）を記録で確かめる
- [ ] AC26: #818 §3 の題材を、指示なしの条件（§3 の B / C と同じ）で Claude Code に 3 回ずつ実行し、Serena または `LSP` のツールが使われた回数と、ツール結果の文字数を、§3 の値と並べて示す
- [ ] AC27: 設計文書の効果の見積もり（持ち場ごと）に、AC26 の実測で置き換えた値を並べる

### 退行しないこと

- [ ] AC28: `claude plugin validate .` が終了コード 0 で終わる。`bash scripts/build-runtime-plugins.sh --check` が通る
- [ ] AC29: Kiro の installer（`plugins/mcp/mcp-serena/dev.kiro/install.sh --dry-run`）が終了コード 0 で終わる
- [ ] AC30: `git ls-files .serena` に `serena_config.yml` が出ない（済。#818 の本文）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | AC23。Serena の初回の `find_symbol` は、採った言語が 2 つのとき 5 秒以内（#818 §8 の 4 言語で 4.2 秒、1 言語で 0.1 秒） |
| 可用性 | 1 言語の言語サーバの失敗が、他の言語のシンボル操作を止めない（AC7・AC8）。hook のスクリプトが失敗しても、ツールの呼び出しを止めない（終了コード 0 で何も出さない） |
| 運用・保守性 | 対応する言語を増やすときに変えるのは対応表だけである。ただし、既にある種類の追加の検査（`typescript_major_5` / `shellcheck`）で足りない言語は、新しい種類の検査の関数を `check.py` に 1 つ足す。hook とスクリプトは言語の名前で分岐しない |
| 移行性 | 既に `.serena/project.yml` を持つリポジトリで、`language_servers`・`ignored_paths`・`mcp_serena_excluded` 以外を変えない（AC6）。mcp-serena の更新だけでは既存の `project.yml` を書き換えない |
| セキュリティ | セッションの開始で、ネットワークへ出る導入（`uv tool install`・`npm install -g`・`claude plugin install`）を走らせない。導入は Skill の手順で、利用者の確認を取ってから行う |
| システム環境 | hook と検査のスクリプトは Python 3 の標準ライブラリだけで動く。`uvx` を要するのは Serena の起動と起動の検証だけである |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。mcp-serena の起動引数・hook・Skill が変わる。ツール名の接頭辞 `mcp__plugin_mcp-serena_serena__` は変わらない |
| データ | 導入先の `.serena/project.yml` をスクリプトが書く（Skill を実行したときだけ） |
| 既存の振る舞い | SessionStart の echo 1 行が無くなる。Claude Code では memory のツールが一覧から消える |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest plugins/mcp/mcp-serena -q -n 4` |
| 文書の検査 | `python3 scripts/check-markdown-links.py` / `python3 scripts/check-doc-line-limit.py` / `python3 scripts/check-skill-frontmatter.py` |
| プラグインの検査 | `claude plugin validate .`（終了コード 0）/ `bash scripts/build-runtime-plugins.sh --check` |
| 手動確認 | AC11・AC12・AC24〜AC27。実機の claude は `env -i` と一時の HOME で隔離して動かす |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`。実装は `plugins/mcp/mcp-serena/`、NDF の Skill とエージェント定義は該当行だけ。設計の Pull Request は `issues/` だけ |
| コーディング規約 | スクリプトは Python 3 の標準ライブラリ。hook は失敗しても 0 で終わる（NDF の hook と同じ） |
| テスト戦略 | 判定（検出・しきい値・食い違い・誘導の数え方・YAML の書き換え）は単体、`serena` の起動は偽のコマンドで結合、実機は手動確認。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 対応表を唯一の正本にする。設定を書いた後に起動を検証する |
| 確認してから行う | 公式 LSP プラグインと言語サーバ本体の導入、`.gitignore` と `.serena/.gitignore` の書き換え、Serena の言語サーバのキャッシュの退避 |
| 行わない | セッションの開始での導入。確認なしの `.gitignore` と `.serena/.gitignore` の書き換え。`project.yml` の `language_servers` / `ignored_paths` / `mcp_serena_excluded` 以外の書き換え |
