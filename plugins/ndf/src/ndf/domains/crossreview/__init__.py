"""cross-review（PR を相手役にレビューさせ、指摘を直して収束させる）の部品（#1142 の M5。N6）。

- `repo`: レビューの対象のリポジトリを `--repo`・状態ファイル・cwd の origin の順で決める
- `push`: 修正の push。拒まれたらマージして 1 回だけ送り直す
- `store`: 状態ファイルと修正の結果ファイル。巻き直しの後の番号を元の番号へ引く
- `fixgate_decide`・`scope_decide`・`finish_decide`: 修正の記録の要否・レビューの範囲・終わり方の判断（純粋な関数）
- `settings`・`changes`: 起動の引数とレビューの設定・変更の読み取り
- `drive`: 収束ループの駆動（相手役の起動・指摘のジャーナル・修正担当の起動・push・記録）
- `cli`: サブコマンド `cross-review`

レビュー worktree の lock・同期・片付けと、相手役の割り当て・振動の判定は cross 系で共通の `ndf.core.converge` が持つ。
"""
