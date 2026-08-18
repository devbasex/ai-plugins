# cross-refactoring 再々検証（3 回目）

`/ndf:cross-refactoring` の**未到達 2 経路**を実機で通すための Pull Request。
経緯は [引継ぎメモ](issue-113-cross-refactoring-re-retrial-handoff.md) にある。

## 通したい経路

| 経路 | 何が観測できれば通ったと言えるか |
| --- | --- |
| 指摘の修正と再レビュー | `merge-fix` が修正コミットを取り込み、再レビューで判定が変わる |
| 上限到達時の項目単位の見送り | `should-abandon` が上限到達を返し、`abandon-items` が未解決の項目だけ取り消す |

あわせて、PR #121 で入れた次の 3 経路も実機で確認する。

| 対象 | 確認内容 |
| --- | --- |
| 公開の責務 | 実装担当が push せず、`merge-apply` の後に進行側が push する |
| 生成物の同期 | 同期コミットが push の直前に積まれる |
| 対象外への記録 | 適用で失敗した項目が次ラウンドの提案で再採用されない |

## 実行条件

2 回目はラウンド 1 で両レビュー担当とも指摘 0 件だったため、修正と見送りの経路へ
到達できなかった。3 回目は指摘が出る確率を上げる 2 つの調整を併用する。

| 引数 | 値 | 狙い |
| --- | --- | --- |
| `--max-fix-rounds` | `1` | 指摘が 1 回の修正で解決しなければ、即 `should-abandon` へ到達する |
| `--max-items-per-round` | `8` | 採用件数を増やし、指摘が出る確率を上げる |
| `--max-outer-rounds` | `3` | 既定どおり |

```bash
export PLUGIN_ROOT=/work/ai-plugins/plugins/ndf-claude

/ndf:cross-refactoring <PR番号> \
  --scope plugins/ndf-shared/skills/cross-refactoring/scripts \
          plugins/ndf-shared/skills/cross-refactoring/tests \
          plugins/ndf-shared/skills/cross-review/scripts/lib \
          plugins/ndf-shared/skills/cross-review/tests \
  --sync-command "bash scripts/build-runtime-plugins.sh" \
  --baseline-test "uv run --with pytest python -m pytest \
      plugins/ndf-shared/skills/cross-refactoring/tests \
      plugins/ndf-shared/skills/cross-review/tests -q" \
  --max-outer-rounds 3 --max-fix-rounds 1 --max-items-per-round 8
```

着手前のテストは 444 件が通ることを確認済み。
