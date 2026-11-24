# 用語集: NDF の worktree（`ndf-worktree`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

worktree の設定・セッション開始 hook・テスト環境の割り当ての語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| worktree | `worktree` | 開発の変更を行う git worktree。課題ごとに 1 つ切る | 作業ツリー | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| メインディレクトリ | — | リポジトリを clone したディレクトリ | 主ディレクトリ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worktree レジストリ | `registry` | 割り当て・ポート・基準のタグ・公開の記録を持つ JSON（.git/ndf/worktree-registry.json） | — | — | `docs/specifications/ndf-testenv-lock-and-registry.md` |
| 共有設定 | — | .ndf/worktree.json。git で追跡する | 共有の宣言 | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 個人設定 | — | .ndf/worktree.local.json。git で追跡しない | 個人の宣言 | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 実効設定 | — | wt_declaration が返す、共有設定に個人設定を反映した JSON | 重ね合わせた宣言 | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 追従 | — | セッション開始 hook がメインディレクトリの HEAD を git checkout --detach で動かすこと（follow_branch） | — | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| セッション開始 hook | — | worktree-session.sh。Claude Code / Codex の SessionStart、Kiro の agentSpawn などで動く | 開始時の hook | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 開発 worktree | — | 人が変更を加えて Pull Request にする worktree。ブランチを持つ | 開発用の worktree | — | `plugins/ndf/skills/worktree/SKILL.md` |
| レビュー worktree | — | cross-review と cross-refactoring が一時的に使う worktree。システムの一時ディレクトリに置く | レビュー用の worktree | — | `plugins/ndf/skills/worktree/SKILL.md` |
| 依存物 | — | パッケージマネージャが入れる、追跡されないディレクトリ（`vendor/`・`node_modules/`・`.venv` など）。worktree には最初から無い | — | — | `plugins/ndf/skills/worktree/SKILL.md` |
| 依存の用意 | — | worktree を作った直後、または未用意の既存 worktree を使う前（使い回し・手で打つやり直し）に、設定に従って依存物を worktree で使える状態にすること。手段（コマンド・参照・複製）は問わない | — | — | `plugins/ndf/skills/worktree/SKILL.md` |
| テスト環境 | — | 1 つの worktree に対応して立ち上げる、テスト実行に必要なサービスの一式。番号で識別され、他の worktree とポート・データ保存先・ネットワークを共有しない | — | — | `plugins/ndf/skills/worktree/references/test-execution.md` |
| テスト環境の用意 | — | 到達の確認が通らないときに、テスト環境を起動して到達を確かめ直すこと | — | — | `plugins/ndf/skills/worktree/references/test-execution.md` |
| テスト環境のスロット | — | テスト環境へ割り当てる 0〜63 の番号。worktree ごとのポートの計算に使う | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| テスト環境の割り当て | — | worktree とテスト環境の環境名・スロット・ポートの対応。worktree レジストリの 1 行で、解放の時刻が空の間は有効である | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 残った割り当て | — | 解放の時刻が空で、worktree のディレクトリが無く、git worktree lock もされていない割り当て | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| テスト環境の回収 | — | 残った割り当てのテスト環境を compose の定義なしで破棄し、解放の時刻を書くこと | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 見えない位置の割り当て | — | worktree のパスがメインディレクトリの下に無く、親のディレクトリが無いか、親のディレクトリがメインディレクトリと別のファイルシステムにある割り当て。回収を打った位置から存在を確かめられないため、残った割り当てとして判定しない | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 回収の予約 | — | 環境名ごとのロック。回収が環境名の共有を確かめてから解放を終えるまで持ち、割り当ての追加と使い始めが短く取って通る | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 到達の確認 | — | コンテナの中から worktree の探りのマーカーを読み、テストのコンテナが worktree を見ているかを確かめること | — | — | `plugins/ndf/skills/worktree/references/test-execution.md` |
| ツールのパス | `tool_paths` | CLI が起動したツール（MCP サーバーなど）が、利用者の操作なしに worktree の中で書き換える既知のパス。既定（`.serena/project.yml` と `.serena/serena_config.yml`）と `.ndf/worktree.json` の `tool_paths`（`add` / `remove`）で決まる。検査とコミットの対象から外す | — | — | `plugins/ndf/skills/worktree/references/ndf-settings.md` |
| 残る状態 | — | NDF の実行が作り、その実行が終わった後もディスクに残るもの（レビュー worktree・状態ファイル・一時ファイル・投稿キューの項目）（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 状態のオーナー | — | 残る状態を作った実行。オーナーが生きている間は、別の実行がその状態を消さない（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 状態の寿命 | — | 残る状態を片付けてよくなる条件（オーナーのプロセスの終わり・Pull Request が閉じたこと・送り終えたこと など）。状態の種類ごとに 1 つ決める（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 以前の実行 | — | 今の実行より前に同じ Pull Request・同じリポジトリで回った、終わった実行。supervise.py の run --from の「前の実行」とは別（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| オーナーの記録 | — | オーナーのキーごとに 1 つ、状態の保存先の leases/<リポジトリ>/ に置く記録。実行の識別子・エントリポイント・開始・最後に動いた時刻・終わった時刻・オーナーのプロセス（pid・開始時刻・ホスト名）を持つ。片付けがオーナーの生存を判定するときに読む（#2229・#2342） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 寿命の窓 | — | オーナーの記録が終わっていなくても、最後に動いた時刻からこの長さを過ぎたオーナーを死んでいるとみなす長さ。オーナーの部品（leases/）の記録では別のホストの記録にだけ使い、同じホストの生死は pid と開始時刻で決める。値はオーナーのコントラクトの window_seconds に置く。レビュー worktree は 24 時間（#2229・#2342）、見直しのオーナーは既定 15 分（#2315） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 片付け | — | 状態の一覧に従い、オーナーの終わった残る状態を外し、残したものと理由を返す処理（run_state.sweep_states）。git が拒めば止まる操作だけで行う（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| オーナーのキー | `lease_key` | オーナーの記録を取り合う単位。エントリポイントのコントラクトが、解決した入力から作る形を定義する（例 cross-refactoring:pr=2301。#2335） | — | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| オーナーのコントラクト | — | エントリポイントのコントラクトの行が持つ lease の設定。サブコマンドごとにキーの形・オーナーの単位・寿命の窓・返すサブコマンドかを決める。見直しのオーナーが実行時に取る記録（オーナーの設定）とは別の物である（#2342） | — | — | `docs/specifications/ndf-lease-stage-and-concurrent-runs.md` |
| オーナーの単位 | — | オーナーの記録を誰が持つか。run はエントリポイントの 1 回の実行で、実行の終わりで返す。session はランタイムのセッション（Claude Code・Codex のプロセス）で、返すエントリポイントが呼ばれるかセッションが終わるまで持つ（#2342） | — | — | `docs/specifications/ndf-lease-stage-and-concurrent-runs.md` |
| 割り当て表 | `allocations` | テスト環境の割り当ての一覧（worktree-registry.json） | 台帳 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
