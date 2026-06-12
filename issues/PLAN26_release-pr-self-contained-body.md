# PLAN26: issue-plan-strategy の release PR body を self-contained にする

- issue: https://github.com/devbasex/ai-plugins/issues/28
- 作成日: 2026-06-12
- ステータス: 実装中

## 背景・解決したい課題

`ndf:issue-plan-strategy` の release ブランチ戦略では、release PR の body テンプレートが
「plan ファイルへの参照 + 個別 PR チェックリスト」中心になっている。

しかし実運用では:

- 子 PR (base=release) は cross-review 等の**セルフレビューで merge** される
- **人間のレビュアーが見るのは release PR だけ**であり、子 PR の存在を意識させるべきでない

その結果、現行テンプレートのままだとレビュアーが PR 単体で変更の目的・内容を把握できず、
レビュー前に body の手動書き直しが必要になる (carmo-system-console PLAN042 で実際に発生)。

## 変更内容

`plugins/ndf/skills/issue-plan-strategy/SKILL.md` に以下を追記・修正する:

1. **レビュアー視点の原則を明文化**
   - 「子 PR はセルフレビューで merge される。人間のレビュアーが見るのは release PR だけ。
     子 PR の存在をレビュアーに意識させない」を Step 3 に原則として記載
2. **release PR body の self-contained 必須化**
   - Step 3 の body テンプレートを「何のために (背景)」「何を (release 全体の変更内容)」中心に変更
   - 子 PR チェックリストは `<details>` 折りたたみ内の開発用進捗管理に格下げ
3. **body 最終化ステップの明記**
   - Step 8 の Draft 解除前に「実装の最終形を反映した self-contained な body へ更新する」工程を追加
   - cross-review light rotation (Step 6b) と同等の「現状の差分・実装を反映 / 内部用語を漏らさない」原則を適用
4. **アンチパターン追記**
   - 「release PR body が子 PR リンクの列挙だけ」を追加

付随変更:

- `plugins/ndf/.claude-plugin/plugin.json`: version 4.12.1 → 4.13.0
- `.claude-plugin/marketplace.json`: ndf description の版数表記を v4.13.0 に更新

## PR 分割計画

変更対象は skill 1 ファイル + バージョン表記のみで結合度が低く、差分も小さいため
**単一 PR** とする (release ブランチは作成しない)。

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | feature/PLAN26-release-pr-self-contained-body | SKILL.md 修正 + version bump | なし | - |

base branch: `main`

## テスト計画

- [ ] `claude plugin validate` が成功する
- [ ] SKILL.md の修正内容が issue #28 の提案 3 点をすべてカバーしている
