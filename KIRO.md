# AI Plugins - Kiro CLI の扱い

共通の開発ガイドラインは **@AGENTS.md** を参照してください。

このファイルには Kiro CLI に固有のことだけを書きます。

## 配布しない。相手役として起動する

NDF とマーケットプレイスのプラグインは Kiro CLI へ配りません（配布先は Claude Code と Codex）。Kiro CLI は、cross-review のレビュー担当と cross-refactoring の提案・実装の担当（相手役）として、ホスト（Claude Code・Codex）から CLI プロセスで起動されるだけです。

- 起動の形（`kiro-cli chat --no-interactive --trust-all-tools …`）の正本は `plugins/ndf/skills/external-ai/references/cli-kiro.md` にあります
- 相手役に `--agent` は渡しません。相手役が読む手順書は、ホストの導入先から担当の worktree の `.kiro/skills/` へ写します（`plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh`）
- 相手役に使えるかは `.ndf/runtimes.json` と `kiro-cli` の認証で決まります

## 既に Kiro CLI へ NDF を入れた利用者

配布を終えた版より前に `plugins/ndf/dev.kiro/install.sh` で入れた手元の残し方と消し方は、`plugins/ndf/README.md` の「agy・Kiro CLI への配布の終了」にあります。
