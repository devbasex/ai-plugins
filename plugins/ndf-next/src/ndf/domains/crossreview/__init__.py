"""cross-review（PR を相手役にレビューさせ、指摘を直して収束させる）の部品（#1142 の M5。N6）。

- `repo`: レビューの対象のリポジトリを `--repo`・状態ファイル・cwd の origin の順で決める
- `worktree`: レビュー worktree の登録の確かめ・lock・origin への同期・片付け
- `push`: 修正の push。拒まれたら取り込んで 1 回だけ送り直す
- `store`: 状態ファイルと修正の結果ファイル。巻き直しの後の番号を元の番号へ引く
- `fixgate_decide`・`scope_decide`・`finish_decide`: 修正の記録の要否・レビューの範囲・終わり方の判断（純粋な関数）
- `cli`: 副命令 `cross-review`
"""
