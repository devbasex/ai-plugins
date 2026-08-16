# cross-refactoring 修正後の再検証

不具合 9 件の修正（[issue-113-cross-refactoring-defect-fixes.md](issue-113-cross-refactoring-defect-fixes.md)、
NDF v8.2.0）を実機で確かめる。**この Pull Request がその対象**である。

前回（PR #118）は適用結果の検証で失敗した項目を取り消す経路が破綻し、
レビューフェーズより先へ一度も進めなかった。今回の目的は**その先を通すこと**にある。

## 実行条件

| 項目 | 値 |
| --- | --- |
| ホスト | Claude Code（提案・レビューには不参加） |
| 提案・レビュー | codex / gemini / kiro |
| 適用の母集合 | claude / codex / kiro |
| 使用する版 | リポジトリ内の `plugins/ndf-claude`（v8.2.0）。プラグインキャッシュ（8.1.0）は使わない |
| ラウンド上限 | 3 |

```bash
PLUGIN_ROOT=/work/ai-plugins/plugins/ndf-claude

/ndf:cross-refactoring <この PR 番号> \
  --scope plugins/ndf-shared/skills/cross-refactoring/scripts \
          plugins/ndf-shared/skills/cross-refactoring/tests \
          plugins/ndf-shared/skills/cross-review/scripts/lib \
  --baseline-test "uv run --with pytest python -m pytest \
      plugins/ndf-shared/skills/cross-refactoring/tests \
      plugins/ndf-shared/skills/cross-review/tests -q"
```

`--scope` に**テストの置き場所を含める**。範囲は適用結果の検証にも効くようになったため、
含めないと `test_gap` が真の項目で「テストを先に足せ」と「範囲外を触るな」が両立しない。

## 確かめること

修正した 9 件が実機で成立するか。

| # | 直したこと | 何が観測できれば通ったと言えるか |
| --- | --- | --- |
| 1 | 取り消しの巻き戻しと積み直し | 検証に失敗した項目だけが消え、合意済みの項目が残る。分離できない位置関係なら `rounds[].drops[].mode = round` が記録される |
| 2 | 中断と全件失敗の区別 | 取り消しに失敗したら終了コード 4 で進行が止まる（次の提案ラウンドへ進まない） |
| 3 | 判定の逐次記録 | `rounds[].apply_progress` に項目ごとの判定が残る |
| 4 | 再送信の印 | 取り消しが Pull Request へ反映される。`pending_push` が残らない |
| 5 | 範囲の検査 | 配布物 3 系統が実装担当の差分に現れない |
| 6 | 提案結果のラウンド別保存 | `<ランタイム>-propose-rf<ID>-r<ラウンド>-result.json` が巡ごとに残る |
| 7 | gemini の読み取り | gemini の提案が語彙内で返る（`read_file` が拒否されない） |
| 8 | 語彙の列挙 | 3 ランタイムとも `smell` / `technique` が英字の識別子で返る |
| 9 | 認証の確認 | 初期化時に 4 CLI の認証状態が出力される |

## 前回まったく実行できていない範囲

ここを通すことが今回の主目的である。

- レビュー担当 2 者の並列実行と指摘の投稿、承認判定
- 指摘の修正と再レビューの繰り返し、上限到達時の項目単位の見送り
- 実装担当の輪番
- 提案の重複率による収束判定
- 集計値（`report --metrics`）の出力

## 結果

（実行後に追記する）
