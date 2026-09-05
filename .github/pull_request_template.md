## Summary

<!-- 何のために、何を変えたかを 2〜5 行で書く。識別子ではなく、業務の言葉で書く -->

関連する issue: <!-- Closes #NNN / Refs #NNN。閉じる語は番号ごとに要る（`Fixes #12, #13` は 12 だけが対象） -->

## Test plan

<!-- 実行したコマンドと結果を書く。実行していないものはチェックを付けず、理由を残す -->

- [ ] `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q`
- [ ] `python3 scripts/check-skill-frontmatter.py`
- [ ] `python3 scripts/check-doc-staleness.py`
- [ ] `python3 scripts/check-markdown-links.py --root .`

未検証の項目:
既存の失敗:
範囲外と判断したもの:

## 影響範囲

どの配布物が変わるかを書く。変わらないものは「変わらない」と書く。

| 配布先 | 変わるか |
| --- | --- |
| Claude Code | |
| Codex | |
| Kiro CLI | |
| agy | |

## 版を上げる必要があるか

<!-- 「要る」「要らない」のどちらかと、その理由を 1 行。版はまとまり単位でマージが終わった後に
上げる（Pull Request ごとには上げない）。手順は /ndf:release にある -->

<!-- I want to review in Japanese. -->
