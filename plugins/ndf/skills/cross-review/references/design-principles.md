# 設計方針

長丁場が予想されるため **メインセッションの context 消費を最小化** する:

| 観点 | 方針 |
|---|---|
| 投稿の担い手 | **GitHub と git へ書くのはレビューを回す側だけ**（#730）。担当は指摘の控えと結果ファイルを書き、取り込み（`read-result`）が組み立てて待ち行列から送る。修正の担当はコミットまでで、送信・返信・決着・まとめは `merge-fix` が行う。書き込みと記録が同じ手順で続くため、担当が途中で止まっても投稿だけが残らない |
| 投稿の記録 | 参照は送信の応答から、件数は送れたインラインの数から取る。本文は取り込みのプロセスの中だけを通り、メインの応答に載らない |
| 修正 | **必ず worker（`general-purpose` のサブエージェント）で実行**。メイン context に diff は載せない |
| ユーザ問い合わせ | 自動判断を最大化（`critical`/`major`/`minor` は自動修正、ループ中の `nit` は deferred） |
| 取りこぼし防止 | **ループ終了時（approved / max_rounds / oscillation / error いずれも）に最終スイープを必須実行**。`/ndf:fix` を再実行し、残った open review thread（最終 APPROVE ラウンドの minor/nit インラインコメント含む）を **全て解消**。修正可能なものは修正 + push、判断保留 nit も reply + resolveReviewThread して **open thread 0 で終了**。件数は `state.py verify-sweep` が GitHub 側の実数で確認する |
| 再開時の引き継ぎ | 再開の時点で残っていた未解決の指摘は `carried_over` に記録し、**修正の工程を 1 度通すまで収束させない**。増えるラウンドは最大 1 回。通した後の再開では、新しい指摘が出ていなければ抑止しない |
| 状態の永続化 | `<worktree>/.cross_review/cross-review-pr<番号>-state.json` に集約。中断・再開可能 |
| 長尺PR対策 | **`--rotate-after` ラウンドで PR をローテーション**（default=light: 同ブランチで PR 巻き直し / squash: 新ブランチ + squash 統合） |
| 振動検知 | 前のラウンドと**同じ箇所を指す指摘**が 50% 以上なら中断（測り方は [docs/01](../docs/01-state-and-review.md) の Step 4） |
| 終了基準 | **新しい指摘が出なくなったら収束**。全員 `APPROVE` は最も止まらない参加者に律速される。3 つの層の順序は [docs/01](../docs/01-state-and-review.md) の「終了基準」 |
| レビュワーの母集合 | **claude / codex / kiro とホスト**（agy は `--include agy`）から、使える者を決めて毎ラウンド 2 席。使える者の解決と席の埋め方は [docs/05](../docs/05-pool-and-convergence.md) |
| 2 ラウンド目以降 | 既存コメントの控えを取り直す（[docs/01](../docs/01-state-and-review.md) の Step 1） |
