"""worktree のコンテキスト（作業場所の用意と片付け）。

判断（純粋な関数。名前が `_decide`）:

- `guard_decide`: 書き込み先がメインディレクトリの許可パスの外か・案内の文
- `session_decide`: セッションを始めたときに伝える、メインディレクトリの逸脱（未コミットの変更）
- `cleanup_decide`: 開発 worktree を消すか残すかと、その理由

部品:

- `declaration`: 宣言 `.ndf/worktree.json` と個人設定 `.ndf/worktree.local.json` の読み取り
- `repo`: メインディレクトリ・worktree の中か・worktree の一覧（git は `adapters.git` を通す）
- `create`: ブランチの worktree を `.worktrees/<ブランチ>` に作る（作業場所の用意）
- `cleanup`: マージ済みのブランチの worktree を消す（PR は `adapters.github` から読む）
- `cli`: `ndf worktree` の副命令
"""
