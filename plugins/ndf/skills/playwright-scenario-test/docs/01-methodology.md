# 01. テスト方法論総論

本 Skill は「Playwright + curl で実行する E2E テスト」を、経験則ではなく**業界標準の理論**に基づいて設計する。
本書はその理論的な土台を、実用に落とし込む形で要約したもの。

## 1. 目的の二項定義

テストの目的は次の 2 つに分解できる (ISTQB / Beizer)。

1. **不具合の発見**: 仕様 / 期待挙動から外れる症状を能動的に探す
2. **修正担当者への情報提供**: 発見した症状を**再現可能で偏りのない記述**で伝える

この 2 つは独立したスキルセットを必要とする。本 Skill は両方を「設計時にどの観点をテストするか」と「実行時に何を証拠として残すか」の 2 段で機械化する。

## 2. テスト戦略のフレームワーク (HTSM)

James Bach の **Heuristic Test Strategy Model** (v6.3) は、テスト戦略を 4 つの構成要素に分解する。

```
            Quality Criteria       (CRUSCSPCID: どの品質特性をテストするか)
                  │
   Mission ─── Strategy ─── Test Techniques  (どう調査するか)
                  │
            Project Environment    (リソース・制約・チーム・予算・スケジュール)
                  │
            Product Elements       (SFDIPOT: 何をテストするか)
```

### 2.1 Product Elements (SFDIPOT)

「製品の何を見るか」のチェックリスト。

| 因子 | 内容 | Web E2E での例 |
|---|---|---|
| **S**tructure | 構造的部品 | URL ツリー / route 設計 / DOM 構造 / コンポーネント階層 |
| **F**unction | 機能 | CRUD / 検索 / 認証 / 決済 / 通知 |
| **D**ata | データ | 入力 / 出力 / 永続化 / 流入元 / 文字種・境界 |
| **I**nterfaces | 接合面 | API / WebSocket / 3rd party / iframe / postMessage |
| **P**latform | プラットフォーム | OS / ブラウザ / device / viewport / 言語 |
| **O**perations | 運用 | 利用シナリオ / ペルソナ / ロール |
| **T**ime | 時間 | 日付 / TZ / 同時実行 / sequence / 期限 |

### 2.2 Quality Criteria (CRUSCSPCID)

「どんな品質か」のチェックリスト。本 Skill は **Capability** (機能満足) と **Reliability** (再現性) を主軸に、Web 文脈で重要な **Usability** (a11y 含)、**Security**、**Performance** を併走させる。

| 略 | 軸 | 主担当チェック |
|---|---|---|
| C | Capability | 機能要件を満たすか |
| R | Reliability | 同じ操作で同じ結果か。エラー時に回復するか |
| U | Usability | 操作可能性 (a11y / キーボード操作 / 国際化) |
| S | Scalability | 大量データ / 高負荷下の挙動 |
| C | Charisma | 感情的訴求 / ブランド整合 (人間判定主体) |
| S | Security | OWASP Top 10 / CSRF / IDOR / 認証 |
| P | Performance | LCP / INP / CLS / API 応答時間 |
| C | Compatibility | クロスブラウザ / OS / デバイス |
| I | Installability | (SaaS では設定/退会フローに相当) |
| D | Development | テスト容易性 / ログ充実 |

## 3. テスト技法 (ISTQB CTFL 4.2)

詳細は `03-test-techniques.md` 参照。本書では「どの page role でどの技法を必須にするか」だけ示す。

| page role | 必須技法 | 推奨追加 |
|---|---|---|
| LP | Claims Testing, Domain Testing (viewport) | accessibility / web_vitals |
| list | Equivalence Partitioning, BVA, Pairwise (フィルタ次元 ≥3) | State Transition |
| item | Domain Testing (id partition), Risk Testing (IDOR) | Claims |
| edit | BVA, Equivalence Partitioning, Decision Table | State Transition (dirty/saving/error) |
| form | **Decision Table 必須**, Classification Tree, State Transition | Pairwise |
| search | Domain Testing, Claims Testing | Pairwise (ファセット) |
| dashboard | Domain Testing (期間), Claims Testing | State Transition |
| auth | Decision Table (認証分岐), Risk Testing | |
| cart/checkout | Decision Table, BVA (金額境界), State Transition | |
| modal/wizard | State Transition (open/close/focus), ARIA APG conformance | |

## 4. Oracle: FEW HICCUPPS (Bach / Bolton)

「これは不具合か?」を 11 軸で判定する。**全 bug report に該当軸を必ず記録**することで、AI / 人間の判定揺らぎを抑える。

| 略 | 軸 | 例 |
|---|---|---|
| **F** | Familiarity (既知パターン) | 過去 bug DB に同型がある |
| **E** | Explainability (説明可能性) | 価格と総額の差を説明できない |
| **W** | World (世界の事実) | 月が 13 月、県が 47 以外 |
| **H** | History (過去版との一貫性) | 前 release ではできた操作 |
| **I** | Image (ブランド/外観) | デザインガイド逸脱 |
| **C** | Comparable products | 競合と比べ明らかに弱い |
| **C** | Claims (仕様/広告) | spec が「3秒以内」と主張 |
| **U** | User expectations | 一般的ユーザが「こう動く」と思う |
| **P** | Product (内部一貫性) | 詳細とリストで値が違う |
| **P** | Purpose (目的整合) | EC なのに購入できない |
| **S** | Statutes/Standards (法令/標準) | WCAG / GDPR / PCI 違反 |

## 5. Hendrickson Test Heuristics Cheat Sheet

20 個の guideword で「テストアイデアを生成する」発想支援。実行のたびに「Boundaries / Goldilocks / CRUD / Position / Selection / **Count (0/1/Many)** / Multi-user / Flood / Sequences / Sorting / **Interruptions** / Constraints / Input Method / Configurations / Starvation / Dependencies / Touch Points / Variable Analysis / **State** / Map Making」をチェック。

太字の **Count**, **Interruptions**, **State** は Web E2E で見落としやすく、checklists/ で繰り返し参照する。

## 6. テスト計画立案フロー (本 Skill の標準手順)

```
[1] 対象 URL のスクショまたは構造解析
       │ Playwright: page.accessibility.snapshot() or page.evaluate(getRoleSummary)
       ▼
[2] page role を判定 → docs/02-page-roles.md の識別ヒューリスティック
       │ scripts/classify_page_role.py が補助 (DOM の role 集計)
       ▼
[3] 該当 checklist を開く → docs/checklists/checklist-{role}.md
       │ 全項目を「適用」または「不適用 (理由付き)」と判定
       ▼
[4] 各項目に適用するテスト技法を選ぶ → docs/03-test-techniques.md
       │ 例: 編集フィールドなら BVA で min-1/min/min+1/max-1/max/max+1
       ▼
[5] pytest テストを書く → templates/test_{role}.py.template
       │ playwright codegen で操作録画 → そのまま test 関数に貼ってもよい
       │ @pytest.mark.page_role(...) を付けると accessibility / web_vitals が autouse で走る
       ▼
[6] 実行 → uv run pytest --pwk-config=./scenario.config.yaml -n 4
       │ trace.zip / video / screenshot / HAR / console / accessibility / web_vitals を自動収集
       │ reports/<run-id>/report.md が pytest_terminal_summary で生成
       ▼
[7] FAIL → docs/05-bug-report.md の構造で報告 (FEW HICCUPPS 軸付与)
```

## 7. 「経験」と「理論」の境界

本 Skill では次の方針で経験則を切り出している。

| 種類 | 配置 | 理由 |
|---|---|---|
| 業界標準 (ISO / WCAG / OWASP / ISTQB) | docs/ 配下 (このディレクトリ) | 出典がある。揺らぎが小さい |
| ヒューリスティクス (HTSM / FEW HICCUPPS / Hendrickson) | docs/ 配下 | 「思考の道具」として再利用 |
| 個別プロジェクトの慣習 (PHP / Rails / 等) | scenario.config.yaml の `tolerated_console_errors` / `tolerated_page_errors` 正規表現 | 個別カスタマイズ |
| 動画/HUD の細かい数値 (字幕高さ・カーソル色) | playwright_kit/overlay.py のコード内定数 | 表示 UX の調整。理論の対象外 |

「経験」を docs に書くのではなく、**理論を docs に書き、慣習は config に逃がす** のが本 Skill の規律。

## 参考文献

- James Bach, "Heuristic Test Strategy Model" v6.3, https://www.developsense.com/resource/htsm.pdf
- Michael Bolton, "FEW HICCUPPS", https://developsense.com/blog/2012/07/few-hiccupps
- Elisabeth Hendrickson et al., "Test Heuristics Cheat Sheet", https://www.ministryoftesting.com/articles/test-heuristics-cheat-sheet
- ISTQB Foundation Level Syllabus 4.2 (Black-box Test Techniques), https://astqb.org/4-2-black-box-test-techniques/
- ISO/IEC/IEEE 29119-3:2021 — Test documentation
- W3C, "WCAG 2.2", https://w3c.github.io/wcag/requirements/22/
- OWASP, "Top 10:2025", https://owasp.org/Top10/2025/0x00_2025-Introduction/
