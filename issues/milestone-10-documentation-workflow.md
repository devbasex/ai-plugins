# マイルストーン 10: ビジネス文書ワークフロー — 要求と受け入れ条件

親 #506 と子 9 件（#507 / #508 / #509 / #510 / #511 / #512 / #513 / #514 / #515）を、
**設計 1 本 + 実装 4 本**の Pull Request で通す。

## 依頼（原文）

> /ndf:development-workflow https://github.com/devbasex/ai-plugins/milestone/17

マイルストーンの説明（原文）:

> 提案資料・プレゼン・KPI シート・運用マニュアルなどのビジネス文書を作るための工程と Skill。
> 企画承認と制作物承認の 2 つの関門を持つ。

## 目的

**ビジネス文書を作る工程を、開発の工程表へ 5 番目のモード（`documentation`）として足す。**
中間形式を Markdown にしてリポジトリへ格納することで、`worktree` `cross-review` `pr` `merged`
がそのまま効く。

| 課題 | 達成したい状態 |
| --- | --- |
| #507 | `development-workflow` が 5 モードを判定し、工程表が 18 行 × 5 列になる |
| #508 | 企画承認と制作物承認が、既存の 2 つの関門への写像で成り立つ |
| #509 | 使う数値と引用の出所が残り、事実確認が完了判定の段になる |
| #510 | 執筆の Skill が 6 タイプの参照を持ち、生成先に依らない Markdown を書かせる |
| #511 | 生成と提出が `release` の形として足され、新しい Skill が増えない |
| #512 | 工程名の並びを持つ 4 箇所が揃い、新設 Skill が配布物に載る |
| #513 | 体裁設計が `design` に入り、体裁レビューが描画した結果を見る |
| #514 | 外部にある既存の文書が取り込まれ、内容のレビューが差分に載る |
| #515 | ドキュメンテーションシステムが 1 システム 1 ファイルで分かれる |

## モード判定

```text
mode: standard
根拠: 公開インタフェース（Skill と工程表の値）を追加し、複数モジュール
  （development-workflow / projects-common / design / release / quality-gates）にまたがる
必須工程: worktree → requirements-design → design → document-restructuring
  → pr → cross-review → merged（設計 Pull Request） → 実装用の worktree
  → implementation-plan → tdd-cycle → cross-refactoring → cross-review
  → quality-gates → pr → plan-to-spec → merged → release
  → release-verification → retrospective
```

**5 本すべてが `standard` である。** 判定の単位は Pull Request で、束ねた 5 本のいずれも
「公開インタフェースの追加」と「複数モジュールにまたがる変更」に当たる。`light` へ倒せる
Pull Request は無い。

## 束ね方

**設計を 1 本にまとめる。** 9 件の「決めること」は相互に依存する。#513 の
`layout-<出力の形>.md`・#511 の `form-<出力の形>.md`・#515 の `system-<名前>.md` は
**同じ対象を 3 つの軸で切る**ため、別々に設計すると境界がずれる。

**実装は 4 本に分ける。** 触るファイルが重ならない単位で切った。

| # | ブランチ | 閉じる課題 | 主に触るもの |
| --- | --- | --- | --- |
| 設計 | `design/milestone-10-documentation-workflow` | **閉じない** | `issues/` の設計文書 |
| 実装 1 | `feature/issue-507-508-documentation-mode` | #507 / #508 | `development-workflow/`、`workflow-common.sh`、`projects-common.sh` |
| 実装 2 | `feature/issue-515-511-514-systems` | #515 / #511 / #514 | `document-systems/`（新設）、`release/references/` |
| 実装 3 | `feature/issue-509-510-drafting` | #509 / #510 | `document-sources/`・`document-drafting/`（新設）、`quality-gates/` |
| 実装 4 | `feature/issue-513-512-layout` | #513 / #512 | `design/references/`、`layout-review/`（新設）、`manifests/`、`README.md` |

**設計 Pull Request の本文には課題を閉じる語を書かない。** 実装が終わっていない段階で
マージするためである。

**実装 1 が先である。** 工程表の 2 行と 5 列目が決まらないと、他の 3 本が書く参照の
置き場所（どの工程の下にあるか）が決まらない。実装 2 / 3 / 4 は実装 1 のマージ後に始める。

**配布数の締めは実装 4 が持つ。** 新設 Skill は 4 本の Pull Request に散るため、各実装
Pull Request が**自分が新設した分**を `manifests/*-skills.txt` へ載せ、`README.md` の数を
更新する。#512 の受け入れ条件のうち配布物に関わるものは、最後の実装 4 で全体が揃う。

## 前提

- 前提 1: 新設する Skill は 4 個である（`document-sources` / `document-drafting` /
  `layout-review` / `document-systems`）。#506 の本文は本節を「新設は 3 個に絞る」と書いた
  後に #515 で `document-systems` を足しており、**受け入れ条件の側（新設 4 個）を採る**
- 前提 2: 配布数は現行の 41 / 40 / 39 / 39（claude / kiro / codex / agy）から
  **45 / 44 / 43 / 43** になる。#506 と #512 の本文が書く 40 / 39 / 38 / 38 は
  `notion-writing` を足す前の値で、現行と食い違う。**実測した現行の行数を基準にする**
- 前提 3: 版を上げるのはマイルストーン全体のマージが終わった後である。5 本それぞれでは
  上げない
- 前提 4: 既に open な設計 Pull Request 3 本（#501 / #502 / #505）は別のマイルストーンの
  作業であり、この束と触るファイルが重ならない
- 前提 5: このリポジトリでは `main` / `develop` が ruleset で保護され、承認 1 件が必須である。
  メンテナーが 1 人の間は `gh pr merge --admin` が唯一の経路になる。**到達点は置き直さない**

## 対象範囲

含む:

- `development-workflow` へ `documentation` モードと 2 つの工程（素材の収集と出典の確定 /
  体裁レビュー）を足す
- 工程名の並びを持つ 4 箇所を揃える
- Skill を 4 個新設する
- 既存 Skill 5 個（`design` / `release` / `quality-gates` / `requirements-design` /
  `cross-review`）へ `documentation` 向けの参照と段を足す
- 提出先の宣言（`.ndf/document.json`）の形を決める

含まない:

- **取得と投稿の実装そのもの。** `document-systems` が持つのは手順の記述であり、
  Confluence / SharePoint のクライアントは書かない
- **既存の実装（`with-ai-dev` / `project-trygroup-prd`）の移植。** 参照として知識を写すに
  とどめ、スクリプトは持ち込まない
- **議事録。** 会議の自動生成物であり、工程を持たない
- **他のモード（`light` / `operation` / `legacy-refactor` / `standard`）の判定基準の変更**
- **版を上げること。** マイルストーン全体のマージ後に別途行う

## 全体の受け入れ条件

各課題の受け入れ条件は issue の本文が持つ。ここには**束ね方を通して初めて確かめられるもの**
だけを書く。

- [ ] 条件 1: `WF_STAGE_MATRIX` が 18 行 × 5 列になり、`SKILL.md` の工程表・`PJ_STAGES`・
      盤面の単一選択の 4 箇所が**並びまで**一致する。突き合わせをテストが行う
- [ ] 条件 2: 新設した Skill が 4 個で、いずれも `manifests/*-skills.txt` の少なくとも 1 つに
      載る。`skills/` の実体と manifest の食い違いをテストが拾う
- [ ] 条件 3: 承認の関門が 2 つのままである。`WF_APPROVAL_LABEL` と `WF_DESIGN_PREFIX` を
      変更していない
- [ ] 条件 4: 3 つの軸（出力の形 / ドキュメンテーションシステム / 文書タイプ）が別の
      ファイル群に分かれ、同じ規約を 2 箇所に持たない
- [ ] 条件 5: `README.md` の公開 Skill 数とカテゴリ内訳が更新され、
      `python3 scripts/check-doc-staleness.py` が通る
- [ ] 条件 6: 各 `SKILL.md` が 500 行以下である
- [ ] 条件 7: 子 issue 9 件がすべて閉じ、親 #506 のチェックリストが埋まる

## 検証手段

| 何を | コマンド |
| --- | --- |
| テスト全体 | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| Skill の frontmatter | `python3 scripts/check-skill-frontmatter.py` |
| 自リポジトリ前提 | `python3 scripts/check-skill-repo-assumptions.py` |
| 説明文書の古さ | `python3 scripts/check-doc-staleness.py` |
| 配布物の整合 | `bash scripts/validate-runtime-plugins.sh` |
| 文書の行数 | `python3 scripts/check-doc-line-limit.py` |
| マーケットプレイス | `claude plugin validate .` |

## 境界

```text
常に行う         … 既存テストの実行、Skill frontmatter の検査、工程名の 4 箇所の突き合わせ
確認してから行う … 提出先の宣言ファイルの新設、配布 manifest への追加、版を上げること
行わない         … 外部システムのクライアント実装、既存 Skill の規約の書き換え、
                   他モードの判定基準の変更
```

## 進行

- [x] 要求と受け入れ条件 — 2026-09-09
- [ ] 作業場所の用意
- [ ] 設計
- [ ] ドキュメント再構成
- [ ] ドキュメントレビュー
- [ ] 実装 1〜4
- [ ] 配布
