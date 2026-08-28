# 配布物の単一ディレクトリ化

ランタイムごとに複製している配布ディレクトリを、プラグインごとに 1 つへまとめる。

## 関連リンク

- [Runtime Plugin Distribution 仕様](../docs/specifications/runtime-plugin-distribution.md) — 現行の配布構成
- [Agent Plugins Specification](https://github.com/agentplugins/agent-plugins-spec) — Amazon / Cursor / Microsoft / OpenAI / Vercel が策定するプラグイン形式
- [Kiro Agent Skills](https://kiro.dev/docs/skills/) — Kiro CLI の Skill 読み込み仕様

## モード

複数ファイルにまたがる構成変更で、利用者の導入手順が変わる。要求整理から検証証跡まで通す工程を取る。

## 目的と非目的

達成したい状態:

- Skill の実体をプラグインごとに 1 箇所へ集約し、ランタイム別の複製を持たない
- Skill を 1 行直したときに、差分が 1 箇所にだけ出る
- ビルドスクリプトから、実行時パスの書き換え処理を取り除く

やらないこと:

- Skill の内容・分割・数の見直し（配布構成だけを扱う）
- 配布するランタイムの追加や削除
- `plugins/ndf-shared/scripts/` に置いたスクリプト本体の書き換え

## 前提

実測で確認した事実を根拠として置く。確認に使ったコマンドと結果を添える。

| # | 事実 | 根拠 |
| --- | --- | --- |
| 1 | Codex は `.claude-plugin/plugin.json` をプラグインマニフェストとして読む | 探索順は `.codex-plugin` → `.claude-plugin` → `.cursor-plugin`。`.claude-plugin` だけを持つプラグインを `codex plugin add` して Skill が公開されることを確認 |
| 2 | Codex は Claude 形式の `.claude-plugin/marketplace.json` を読む | `source` が文字列のマーケットプレイス定義で `codex plugin marketplace add` が成功 |
| 3 | 同一ディレクトリに複数のマニフェストがあるとき、Codex は `plugin.json`（Agent Plugins 形式）> `.codex-plugin` > `.claude-plugin` の順で採る | 版数違いのマニフェストを併置して `codex plugin list` の表示を確認 |
| 4 | Agent Plugins 形式のルートマニフェストは 9 項目のみで、hook と Skill の絞り込みを持たない | Codex バイナリ内の `struct RawAgentPluginManifest with 9 elements`。仕様 §6.1 が `skills/` を固定位置と規定 |
| 5 | Kiro CLI は `.claude/skills/` を読み込まない | `.kiro/skills/` に実体と symlink、`.claude/skills/` に 1 つずつ Skill を置いて `kiro-cli chat` に列挙させ、`.claude/skills/` のものだけ認識されなかった。バイナリ内に `.claude` の文字列が 0 件（`grep -a -o '\.claude' kiro-cli-chat \| wc -l` → 0） |
| 6 | Kiro CLI は `.kiro/skills/` に張った symlink を Skill として読む | 上記と同じ検証で symlink 経由の Skill を認識 |
| 7 | Kiro CLI はモデルへ Skill のパスを `.kiro/skills/<名前>/` として渡し、その配下のスクリプトを実行できる | 検証用 Skill に `scripts/hello.sh` を置き、`kiro-cli chat` から実行させて出力を確認 |
| 8 | `.kiro/skills/<A>/../<B>` は symlink の解決先を経由して、プラグイン内の別 Skill へ届く | `readlink -f .kiro/skills/pathprobe/../playwright-planning` がプラグイン側の実体を返す |
| 9 | Kiro CLI 2.19.1 は Agent Plugins 形式に対応していない | `kiro-cli` / `kiro-cli-chat` / `kiro-cli-term` の 3 つで `plugin.json` `agent-plugins` `dev.kiro` の出現数がいずれも 0 |
| 10 | Claude Code 2.1.240 も Agent Plugins 形式に対応していない | 同じ走査で `agent-plugins` が 0 件、`.claude-plugin/plugin.json` が 28 箇所 |
| 11 | Claude Code はマーケットプレイス定義の未知の項目を警告のみで受け入れる | Codex 用の `policy` / `interface` を含む定義で `claude plugin validate` が warning 付きで成功 |
| 12 | Claude Code はプラグインマニフェストの `mcpServers` に `./mcp.json` を指定できる | Agent Plugins 形式の `mcp.json` を参照する構成で `claude plugin validate` が成功 |
| 13 | playwright-kit の 3 つの配布物は共通編集元と同一内容 | `diff -rq plugins/playwright-kit-shared/skills plugins/playwright-kit-{claude,codex,kiro}/skills` の差分が未追跡の `.venv` のみ |
| 14 | ndf の共通編集元には Skill が 31 個あり、配布は Claude Code 27 / Codex 25 / Kiro 26 で、どの manifest にも載らない 4 個がある | `ls plugins/ndf-shared/skills` が `README.md` を除いて 31 件。`cat plugins/ndf-shared/manifests/*.txt \| sort -u \| wc -l` が 27 で、3 つの manifest の和集合は Claude Code の 27 個と一致する。差の 4 個は `google-auth` / `google-drive` / `ml-model-structure` / `skill-stats` |
| 15 | 利用者は Git リポジトリを直接指して導入し、配布用のアーカイブを作る工程は無い | 導入手順は `/plugin marketplace add https://github.com/devbasex/ai-plugins`、`codex plugin marketplace add https://github.com/devbasex/ai-plugins`、Kiro は `git clone` 後の installer 実行。生成物である現在の配布物も Git で追跡している（`git ls-files plugins/ndf-codex/skills \| wc -l` → 113） |

Kiro CLI は 2.19.1、Codex CLI は 0.148.0、Claude Code は 2.1.240 で確認した。

## 受け入れ条件

- [ ] Skill の実体が、プラグインごとに 1 ディレクトリだけ存在する（`git ls-files` で同名 Skill の重複が出ない）。Task 3 で Codex の `skills` 配列と symlink がどちらも効かないと分かった場合に限り、ndf の Codex 向け 25 個の重複を例外として許す
- [ ] `scripts/build-runtime-plugins.sh` に実行時パスの書き換え処理が残っていない（`rewrite_codex_skill_paths` / `rewrite_kiro_skill_paths` の定義と呼び出しが無い）
- [ ] Claude Code で `claude plugin validate` が全プラグインとマーケットプレイス定義で成功する
- [ ] Codex で全プラグインを導入でき、Skill 数が導入前と一致する
- [ ] Kiro CLI で installer を実行し、`.kiro/skills/` の symlink 数と `kiro-cli chat` が認識する Skill 数が導入前と一致する
- [ ] `scripts/validate-runtime-plugins.sh` が成功する
- [ ] 実行時パスを参照する Skill が、Claude Code / Codex / Kiro CLI のいずれでも参照先へ到達できる
- [ ] どの manifest にも載らない 4 個の Skill が、どのランタイムの公開セットにも現れない（Claude Code 27 / Codex 25 / Kiro 26 が変わらない）

## 採用する構成

プラグインごとに 1 ディレクトリを置き、ランタイム固有のファイルだけを名前空間ディレクトリへ分ける。

```text
plugins/ndf/
├── plugin.json                  # Agent Plugins 形式（条件を満たすプラグインのみ）
├── .claude-plugin/plugin.json   # Claude Code
├── .codex-plugin/plugin.json    # Codex（hook か Skill 絞り込みが要る場合）
├── skills/                      # 配布 Skill の唯一の実体
├── optional-skills/             # どの配布先にも載せない Skill（ndf のみ）
├── scripts/                     # プラグイン直下のスクリプト
├── agents/                      # Claude Code のサブエージェント定義
├── hooks/
│   ├── claude.json
│   └── codex.json
├── manifests/                   # ランタイム別の配布 Skill 一覧
├── dev.kiro/                    # Kiro CLI 用 installer とエージェント定義
└── README.md
```

`dev.kiro` は Agent Plugins 仕様 §8.2 が定める Kiro のクライアント拡張ディレクトリ名で、Kiro の公式ドキュメントが示す配置に合わせる。

`optional-skills/` には、どの `manifests/*-skills.txt` にも載せない Skill を置く。ndf の共通編集元には Skill が 31 個あるが、3 つの manifest の和集合は 27 個で、`google-auth` / `google-drive` / `ml-model-structure` / `skill-stats` の 4 個はどのランタイムへも配っていない（事実 14）。これらを `skills/` へ置くと、公開されるかどうかがマニフェスト側の絞り込みだけに掛かる。`skills/` を配布 Skill の実体だけに保てば、絞り込みの結果によらず 27 / 25 / 26 が変わらず、`skills/` を全件公開するルートマニフェストを ndf へ置く選択肢も残せる。4 個は残す。`docs/specifications/ndf-skill-inventory.md` の判定がいずれも「維持」で、`skill-stats` は Skill 台帳の測定ツール、`google-auth` は playwright-kit の Drive 連携が参照先として案内している依存だからである。

`hooks/` を `claude.json` と `codex.json` に分ける形は、Claude Code がマニフェストの `hooks` フィールドからのパス指定を読むことを前提としている。この前提は Task 3 で実測し、成り立たない場合は Claude Code 用を `hooks/hooks.json` の固定パスに据え置く。

### ルートマニフェストを置く基準

Agent Plugins 形式のルートマニフェストは `skills/` を全件公開し、hook を持てない。この 2 点が支障にならないプラグインにだけ置く。

| プラグイン | ルート `plugin.json` | `.codex-plugin/plugin.json` | 判断の理由 |
| --- | --- | --- | --- |
| `playwright-kit` | 置く | 置かない | Skill 4 個を全ランタイムへ配り、hook を持たない |
| `ndf` | 置かない | 置く | 配布 Skill が Claude Code 27 / Codex 25 と異なり、Codex に完了通知の hook がある |
| `mcp-serena` / `mcp-playwright` | 置かない | 置く | セッション開始時の hook を持つ |
| 上記以外の MCP プラグイン 8 個 | 置く | 置かない | MCP サーバ定義だけを配る |

Kiro CLI がルートマニフェストを読むようになった時点で、`dev.kiro/install.sh` の役割は Kiro 側の導入コマンドへ移せる。

### マーケットプレイス定義

最終形は `.claude-plugin/marketplace.json` 1 つで、`.agents/plugins/marketplace.json` は削除する。Codex が要求する `policy` / `category` / `interface` は同じ定義へ含める。Claude Code はこれらを読み込み時に無視し、検証は warning で通る。

統合は全プラグインの移動が終わってから行う（Task 6）。移行の途中では、Codex 用の定義が指す先とプラグインの実体がプラグインごとに食い違うためである。たとえば playwright-kit だけを移した時点で `.agents/plugins/marketplace.json` を消すと、Codex は `.claude-plugin/marketplace.json` へフォールバックし、ndf のエントリは `plugins/ndf-claude` を指したままになる。Codex 利用者には Claude Code 向けの 27 Skill 構成が入り、Codex 用の hook も届かない。移動の途中は両方の定義を保ち、移し終えたプラグインのエントリだけを新しいパスへ向ける。

## 実行時パス参照の一本化

Skill から参照するパスを、プラグインルート起点から Skill ディレクトリ起点へ変える。Kiro CLI が Skill のパスをそのままモデルへ渡し、`..` が symlink の解決先を経由して隣の Skill へ届くため、ランタイムごとの書き換えが不要になる。

| Skill | 現在の参照 | 変更後 |
| --- | --- | --- |
| `fix` | `${PLUGIN_ROOT}/skills/fix/scripts/` | Skill ディレクトリ直下の `scripts/` |
| `cross-review` | `${PLUGIN_ROOT}/skills/cross-review/scripts` | Skill ディレクトリ直下の `scripts/` |
| `cross-refactoring` | `${PLUGIN_ROOT}/skills/cross-refactoring/scripts` と `${PLUGIN_ROOT}/skills/cross-review/scripts/lib` | Skill ディレクトリ直下の `scripts/` と `../cross-review/scripts/lib` |
| `statusline` | `${PLUGIN_ROOT}/scripts/statusline-switch.sh` | `${CLAUDE_PLUGIN_ROOT}` のまま（Claude Code 専用の Skill） |
| `official-skills-autoloader` | `${CLAUDE_PLUGIN_ROOT}/scripts/` | 変更しない（Claude Code 専用の Skill） |

## 修正対象

- `plugins/playwright-kit-{shared,claude,codex,kiro}/` → `plugins/playwright-kit/`
- `plugins/ndf-{shared,claude,codex,kiro}/` → `plugins/ndf/`
- `plugins/mcp/{shared,claude,codex,kiro}/<プラグイン名>/` → `plugins/mcp/<プラグイン名>/`
- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`（削除）
- `scripts/build-runtime-plugins.sh`
- `scripts/validate-runtime-plugins.sh`
- `scripts/runtime-smoke-test.sh`
- `scripts/check-skill-frontmatter.py`
- `tests/runtime-smoke/adapters/{claude,codex,kiro}.sh`
- `tests/runtime-smoke/assertions/assert-{hook-fixtures,kiro-agent}.sh`
- `.github/workflows/runtime-plugin-*.yml`
- `docs/specifications/runtime-plugin-distribution.md`
- `docs/specifications/ndf-skill-inventory.md` / `docs/ndf-plugin-reference.md`（未配布 Skill のパス参照）
- `AGENTS.md` / `CLAUDE.md` / `KIRO.md` / `README.md`

## PR 分割計画

タスクを 1 つずつ PR に分け、release ブランチへ順に取り込む。個別 PR は `/ndf:cross-review` で
codex と gemini の両方が `APPROVE` に収束してから merge する。

| PR # | branch 名 | タスク | 依存 | 並行可否 |
| --- | --- | --- | --- | --- |
| 1 | `feature/single-dir-playwright-kit` | Task 1: playwright-kit を単一ディレクトリへ | なし | ○ |
| 2 | `feature/single-dir-runtime-paths` | Task 2: 実行時パス参照を Skill 起点へ | なし | ○ |
| 3 | `feature/single-dir-probe` | Task 3: ndf の統合が前提とする 3 つの仕様を実測 | なし | ○ |
| 4 | `feature/single-dir-ndf` | Task 4: ndf を単一ディレクトリへ | PR2、PR3 | × |
| 5 | `feature/single-dir-mcp` | Task 5: MCP プラグイン 10 個を単一ディレクトリへ | PR1 | × |
| 6 | `feature/single-dir-marketplace` | Task 6: マーケットプレイス定義を 1 つへ統合 | PR1、PR4、PR5 | × |
| 7 | `feature/single-dir-build` | Task 7: ビルドと検証を縮小 | PR6 | × |
| 8 | `feature/single-dir-docs` | Task 8: ドキュメントを更新 | PR7 | × |

release branch: `release/single-dir`
base branch: `main`

PR4 は Task 3 の実測結果で構成が変わるため、PR3 の merge 後に着手する。PR5 は PR1 が
検証スクリプトへ入れる新旧両対応の分岐に乗るため、PR1 の merge 後に着手する。

## タスク分解

各タスクの完了時点で、対象プラグインが 3 ランタイムで導入できる状態にする。移動が済んでいないプラグインの導入経路も、同じ時点で保つ。

検証スクリプトの参照パスは、プラグインを移した同じタスクの中で追随させる。`scripts/validate-runtime-plugins.sh` は `plugins/*-shared` から plugin family を検出し、family ごとに `plugins/<family>-{claude,codex,kiro}` の存在を確かめる。MCP も `plugins/mcp/{shared,claude,codex,kiro}/<名前>` の 4 つが揃っていることを前提にしている。`tests/runtime-smoke/` の adapter と assertion も `plugins/ndf-kiro/install.sh` のような旧パスを直に書いている。移動だけを先に済ませると、この検査が消えたディレクトリを探して落ちる。`.githooks/pre-push` が同じスクリプトを呼ぶため、push の前段でも止まる。

### Task 1: playwright-kit を単一ディレクトリへ移す

- **対象ファイル:** `plugins/playwright-kit-*/`、`.claude-plugin/marketplace.json`、`.agents/plugins/marketplace.json`、`scripts/validate-runtime-plugins.sh`、`tests/runtime-smoke/adapters/{claude,kiro}.sh`
- **変更内容:** `plugins/playwright-kit/` を作り、共通編集元の `skills/` を実体として置く。ルートマニフェストと Claude Code 用マニフェストを置き、installer を `dev.kiro/install.sh` へ移す。installer はプラグインルートを親ディレクトリとして解決し、版数をルートマニフェストから読む。2 つのマーケットプレイス定義は保ったまま、playwright-kit のエントリだけを新しいパスへ向ける。あわせて検証スクリプトが playwright-kit を単一ディレクトリとして扱うようにし、smoke test の adapter が参照する installer と検証対象のパスも同じコミットで更新する
- **満たす受け入れ条件:** 1、3、4、5、6
- **進め方:** 移動後に 3 ランタイムで導入検証を行う。Skill の内容は変えない。移動と検証スクリプトの追随を同じコミットに入れ、`bash scripts/validate-runtime-plugins.sh` が成功することを確かめてから次へ進む。この時点では ndf と MCP が旧構成のまま残るため、検証スクリプトは新旧どちらの構成も受け付ける形にする

### Task 2: 実行時パス参照を Skill 起点へ変える

- **対象ファイル:** `plugins/ndf-shared/skills/{fix,cross-review,cross-refactoring}/` 配下の SKILL.md と docs
- **変更内容:** プラグインルート起点の参照を Skill ディレクトリ起点へ書き換える。Claude Code 専用の 2 つは変更しない
- **満たす受け入れ条件:** 7
- **進め方:** 書き換え後、3 ランタイムそれぞれで対象スクリプトが起動することを確認してから Task 3 へ進む

### Task 3: ndf の統合が前提とする 3 つの仕様を実測する

ndf は配布 Skill が Claude Code 27 / Codex 25 と異なり、両ランタイムに hook がある。統合すると `skills/` には 27 個の実体が並ぶため、マニフェスト側の絞り込み、その代替となる symlink の解決、hook のパス指定が効くかどうかで構成が変わる。どれも現在の配布物では使っておらず、前提の事実表にも無い。

- **対象ファイル:** 検証用の一時プラグイン（リポジトリへは残さない）
- **変更内容:** 次の 3 点を実測し、結果を「前提」の事実表へ追記する
  - Codex 用マニフェストの `skills` に配列を書いたとき、Codex が配列の Skill だけを公開するか。現在の `plugins/ndf-codex/.codex-plugin/plugin.json` は `"skills": "./skills/"` というディレクトリ指定で、絞り込みは 25 個だけを物理配置することで実現している
  - Codex が Skill ディレクトリに張った symlink を解決して Skill として読むか。事実 6 で確かめたのは Kiro CLI の `.kiro/skills/` だけで、Codex については実測が無い。`skills` 配列が効かなかったときの代替案がこの前提の上に乗るため、同じ機会に確かめる
  - Claude Code がマニフェストの `hooks` フィールドからのパス指定を読むか。現在の `plugins/ndf-claude/.claude-plugin/plugin.json` に `hooks` フィールドは無く、`hooks/hooks.json` の自動探索に依存している
- **満たす受け入れ条件:** —
- **進め方:** 結果によって Task 4 の構成を選ぶ。`hooks` フィールドが効かない場合は、Claude Code 用を `hooks/hooks.json` の固定パスに据え置き、Codex 用だけを `hooks/codex.json` として明示参照する。`skills` 配列が効かない場合は、Codex へ配る 25 個を `.codex-plugin/skills/` へ並べて `skills` にそのディレクトリを指定する。並べ方は symlink の実測結果で決める

`skills` 配列と symlink の実測結果の組み合わせごとに、採る構成を先に決めておく。

| `skills` 配列 | Codex の symlink 解決 | 採る構成 |
| --- | --- | --- |
| 効く | 問う必要なし | `skills/` の 27 個を実体として置き、`.codex-plugin/plugin.json` の `skills` に 25 個を列挙する |
| 効かない | 解決する | `.codex-plugin/skills/` に 25 個の symlink を張り、`skills` にそのディレクトリを指定する。symlink はビルドで生成し、追跡対象に含める |
| 効かない | 解決しない | `.codex-plugin/skills/` に 25 個の実体を複製する。複製はビルドで生成し、Git で追跡して `--check` の対象に含める |

3 つ目に落ちた場合、Codex 向けだけは Skill の実体が 2 箇所に並ぶ。複製を `.gitignore` で追跡から外す形は採らない。事実 15 のとおり利用者は Git リポジトリを直接指して導入するため、追跡していない生成物は利用者の手元へ届かず、Codex 版の Skill が丸ごと欠ける。配布時にアーカイブを作る運用も、マーケットプレイスにリポジトリの URL を渡す現在の導入手順を変えることになるため採らない。重複は Git で追跡し、受け入れ条件 1 の例外として扱う。重複するのは Codex 向けの 25 個だけで、いま 3 ランタイム分の配布物として追跡している 342 ファイルよりは小さい。

### Task 4: ndf を単一ディレクトリへ移す

- **対象ファイル:** `plugins/ndf-*/`、`.claude-plugin/marketplace.json`、`.agents/plugins/marketplace.json`、`scripts/validate-runtime-plugins.sh`、`scripts/check-skill-frontmatter.py`、`tests/runtime-smoke/adapters/{claude,codex,kiro}.sh`、`tests/runtime-smoke/assertions/assert-{hook-fixtures,kiro-agent}.sh`、`plugins/playwright-kit/skills/{playwright-kit-ops,playwright-evidence}/`、`docs/specifications/ndf-skill-inventory.md`、`docs/ndf-plugin-reference.md`
- **変更内容:** `plugins/ndf/` を作り、配布 Skill 27 個を `skills/` の実体として置く。どの manifest にも載らない 4 個（`google-auth` / `google-drive` / `ml-model-structure` / `skill-stats`）は `optional-skills/` へ移し、削除はしない。`scripts/` も実体として置く。hook 定義は Task 3 の結果に従って配置し、各マニフェストから参照する。Kiro 用の installer・エージェント定義・プロンプトを `dev.kiro/` へ移す。2 つのマーケットプレイス定義の ndf のエントリを新しいパスへ向ける。検証スクリプトの ndf 向けの検査と、`plugins/ndf-kiro/install.sh` や `plugins/ndf-claude/scripts/` を直に指している smoke test の参照も同じコミットで新しいパスへ向ける。`optional-skills/` へ移した 4 個を指している参照も同じコミットで追随させる。`scripts/check-skill-frontmatter.py` は既定で `plugins/*-shared/skills` を走査するため、`plugins/*/skills` と `plugins/ndf/optional-skills` を見る形へ変える（走査先が消えると CI の `runtime-plugin-validate` が終了コード 2 で落ちる）。`playwright-kit` の `playwright-kit-ops` と `playwright-evidence` は `google-auth` の置き場所を `plugins/ndf-shared/skills/google-auth/scripts` として案内しており、`docs/specifications/ndf-skill-inventory.md` は `skill-stats` を同じ形で指している
- **満たす受け入れ条件:** 1、3、4、5、6、8
- **進め方:** Task 2 と Task 3 の完了を前提とする。移動後に 3 ランタイムで導入検証を行い、Skill 数が Claude Code 27 / Codex 25 / Kiro 26 のままで、`optional-skills/` の 4 個がどこにも現れないことを確かめる。`bash scripts/validate-runtime-plugins.sh` が成功することも同じコミットで確かめる

### Task 5: MCP プラグイン 10 個を単一ディレクトリへ移す

- **対象ファイル:** `plugins/mcp/{shared,claude,codex,kiro}/`、`.claude-plugin/marketplace.json`、`.agents/plugins/marketplace.json`、`scripts/validate-runtime-plugins.sh`、`tests/runtime-smoke/adapters/{claude,codex,kiro}.sh`
- **変更内容:** `plugins/mcp/<プラグイン名>/` へ統合する。MCP サーバ定義を Agent Plugins 形式の `mcp.json` に一本化し、各マニフェストの `mcpServers` から参照する。hook を持つ 2 つには Codex 用マニフェストを置く。2 つのマーケットプレイス定義の該当エントリを新しいパスへ向ける。検証スクリプトの MCP 向けの検査（ランタイム別ディレクトリと `.mcp.json` の存在、Kiro installer の走査）と、smoke test が指す `plugins/mcp/{claude,codex,kiro}/mcp-bigquery/` も同じコミットで新しいパスへ向ける
- **満たす受け入れ条件:** 1、3、4、5、6
- **進め方:** サーバが実際に起動することを 1 プラグインで確認してから残りへ広げる。移す途中は新旧のディレクトリが混ざるため、検証スクリプトはプラグインごとにどちらの構成かを見て検査する。全 10 個を移し終えた時点で、新しい構成だけを受け付ける形へ戻す

### Task 6: マーケットプレイス定義を 1 つへ統合する

- **対象ファイル:** `.claude-plugin/marketplace.json`、`.agents/plugins/marketplace.json`（削除）
- **変更内容:** 全プラグインのエントリを `.claude-plugin/marketplace.json` へまとめ、Codex が要求する `policy` / `category` / `interface` を同じ定義へ含める。`.agents/plugins/marketplace.json` を削除する
- **満たす受け入れ条件:** 3、4
- **進め方:** Task 1・Task 4・Task 5 で全プラグインの移動が終わった後に行う。削除の前に、Codex が `.claude-plugin/marketplace.json` へフォールバックして 12 プラグインすべてを導入できることを確かめる

### Task 7: ビルドと検証を縮小する

- **対象ファイル:** `scripts/build-runtime-plugins.sh`、`scripts/validate-runtime-plugins.sh`、`.github/workflows/`
- **変更内容:** ビルドの役割を「マニフェストの `skills` 配列を `manifests/*-skills.txt` から生成する」ことに絞る。ディレクトリの複製処理と実行時パスの書き換え処理を取り除く。検証スクリプトからは、移行の途中で旧構成を受け付けるために残した分岐を落とす
- **満たす受け入れ条件:** 2、6
- **進め方:** 参照パスの追随は Task 1・4・5 で済んでいるため、ここでは縮小だけを行う。生成対象が縮むため、`--check` の対象はマニフェストが中心になる。Task 3 で `skills` 配列が効かないと分かった場合は、生成対象へ Codex 向け Skill ディレクトリの構築（symlink、または Codex が symlink を解決しない場合は実体の複製）が残る。どちらも Git で追跡するため、`--check` の対象にも残す

### Task 8: ドキュメントを更新する

- **対象ファイル:** `docs/specifications/runtime-plugin-distribution.md`、`AGENTS.md`、`CLAUDE.md`、`KIRO.md`、`README.md`、各 README
- **変更内容:** 配布構成・導入手順・編集手順を新しい構成へ書き換える。Agent Plugins 形式との関係を仕様書へ残す
- **満たす受け入れ条件:** —
- **進め方:** `scripts/check-markdown-links.py` でリンク切れを確認する

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| プラグイン名 | 変えない | `ndf` / `playwright-kit` / `mcp-*` を維持する |
| マーケットプレイスの参照先 | `plugins/<名前>-<ランタイム>` から `plugins/<名前>` へ | 導入済みの利用者は再インストールが要る |
| Codex のマーケットプレイス定義 | 全プラグインの移動後に `.agents/plugins/marketplace.json` を削除 | `.claude-plugin/marketplace.json` へ自動でフォールバックする。移動の途中は 2 つの定義を保つ |
| Kiro CLI の installer パス | `plugins/ndf-kiro/install.sh` から `plugins/ndf/dev.kiro/install.sh` へ | 導入手順の案内を更新する |
| Skill の名前と数 | 変えない | 配布先ごとの構成は `manifests/*-skills.txt` で維持する |

参照先が変わるため、ndf は v9.0.0、playwright-kit と MCP プラグインは次のメジャー版として扱う。

## 影響範囲

| 領域 | 影響 |
| --- | --- |
| 追跡ファイル | ndf と playwright-kit で約 620 ファイル、MCP で約 100 ファイルが減る。Task 3 が 3 つ目の結果に落ちた場合は、Codex 向けの複製 25 個分（約 110 ファイル）が戻る |
| リポジトリ容量 | 追跡分 14M のうち約 7M が減る |
| 履歴 | 生成物 3 ディレクトリに積まれていた 140,920 行の追加差分が、以後は発生しない |
| CI | ビルド差分検査の対象がマニフェストだけになり、実行時間が短くなる |
| 利用者 | 再インストールが要る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 実行時パスの変更で、いずれかのランタイムからスクリプトへ到達できなくなる | Task 2 を独立させ、3 ランタイムで到達を確認してから先へ進む |
| Codex 側で Skill が想定より多く公開される | ルートマニフェストを置く基準を守る。ndf は Codex 用マニフェストの `skills` で絞り込む。絞り込みが効くかを Task 3 で実測し、効かない場合は Codex へ配る 25 個だけを `.codex-plugin/skills/` へ並べる |
| `.codex-plugin/skills/` へ symlink で並べても、Codex が解決せず Skill を認識しない | symlink の解決も Task 3 で実測する。解決しない場合はビルドで実体を複製し、Git で追跡する。追跡から外すと、リポジトリを直接指して導入する利用者の手元に生成物が届かない。Task 3 の表に 3 通りの結果と採る構成を書いてある |
| どの配布先にも載せない 4 個の Skill を `skills/` へ置き、絞り込みが効かずに公開される | `optional-skills/` へ分け、`skills/` を配布 Skill の実体だけにする。Task 4 で移し、4 個を指しているドキュメントとスクリプトの参照も同じコミットで追随させる |
| 移行の途中で、移動が済んでいないプラグインの Codex 導入経路が切れる | マーケットプレイス定義の統合を Task 6 へ切り出し、それまでは 2 つの定義を保って移し終えたエントリだけを更新する |
| 移行の途中で、検証スクリプトが消えたディレクトリを探して CI と pre-push が落ちる | 参照パスの追随を移動と同じタスク・同じコミットで行う。移行の途中は新旧どちらの構成も受け付ける形にし、Task 7 で旧構成の分岐を落とす |
| Claude Code がマニフェストの `hooks` フィールドを読まず、hook が発火しなくなる | Task 3 で実測する。読まない場合は Claude Code 用を `hooks/hooks.json` の固定パスに据え置き、Codex 用だけを `hooks/codex.json` として明示参照する |
| Kiro CLI が Agent Plugins 形式へ対応したとき、ルートマニフェストと installer の両方が働いて Skill が二重に載る | installer に、Kiro 側がプラグインを認識している場合は symlink を張らない判定を入れる |
| Skill ディレクトリ内の仮想環境が Kiro のファイル監視を圧迫する | `playwright-kit-ops` の実行環境を Skill ディレクトリの外へ出す。Kiro CLI 2.18.0 が同種の事象を修正している |
| 導入済み環境が古いパスを指したまま残る | 各 README の移行手順に、再インストールの手順を書く |

## 切り戻し手順

タスクごとにコミットを分け、プラグイン単位で戻せるようにする。マーケットプレイス定義の統合は Task 6 に切り出したため、移動のタスクを戻したときは、そのタスクが更新したエントリだけが旧パスへ戻る。データの移行を伴わないため、リバートで元の状態に戻せる。

## 残リスク

| 項目 | 状態 |
| --- | --- |
| Claude Code への実インストール検証 | 未確認。マニフェスト検証までは通っている |
| Agent Plugins 形式の `mcp.json` からの MCP サーバ起動 | 未確認。マニフェスト検証までは通っている |
| Codex のルートマニフェストで hook を載せられるか | 未確認。マニフェストの項目に hook が無いため載らないと見ているが、実行では確かめていない。Codex は hook に承認済みハッシュを要求するため、非対話実行では発火の有無を切り分けられない |
| Kiro CLI が Agent Plugins 形式へ対応する時期 | 未確認。Kiro IDE 1.0.288 で対応済み、CLI 2.19.1 では未対応 |
| Codex 用マニフェストの `skills` 配列による Skill の絞り込み | 未確認。現在の配布物はディレクトリ指定だけを使い、絞り込みは物理配置で実現している。Task 3 で実測する |
| Codex が Skill ディレクトリの symlink を解決するか | 未確認。事実 6 で確かめたのは Kiro CLI の `.kiro/skills/` だけである。Task 3 で実測し、解決しない場合はビルドで実体を複製して Git で追跡する |
| Claude Code のマニフェスト `hooks` フィールドによるパス指定 | 未確認。現在の Claude Code 用マニフェストに `hooks` フィールドは無く、`hooks/hooks.json` の自動探索に依存している。Task 3 で実測する |

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証コマンドと結果が対応している
- [ ] `bash scripts/validate-runtime-plugins.sh` が、移動を伴う各タスクのコミットで成功する
- [ ] `bash scripts/runtime-smoke-test.sh` を 3 ランタイムで実行し、成功する
- [ ] `python3 scripts/check-markdown-links.py` が成功する
- [ ] 残リスクの 7 項目それぞれについて、確認済みか未確認かが本文に反映されている
