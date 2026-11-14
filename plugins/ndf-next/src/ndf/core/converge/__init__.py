"""cross 系（cross-review・cross-refactoring）の収束ループが共通に使う部品。

- `participants`: 相手役の割り当て（ランタイムの表の既定の席から、修正担当を除く）と、相手役の 1 回の起動の組み立て
- `oscillation`: 指摘の同じさの鍵と、振動（前々ラウンドの指摘が前ラウンドで消えて戻った）の判定
- `worktree`: レビュー worktree の登録の確かめ・lock・origin への同期・片付け

作業ごとの部品（`ndf.domains.crossreview` など）はここを使い、兄弟の `domains` を import しない。
"""
