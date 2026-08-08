# NDF Skill 棚卸台帳

Skill ごとの実測値と、維持・統合・削除・発動改善の判定を記録する。判定の基準そのものは
[frontmatter 規約](../../plugins/ndf-shared/skills/README.md) ではなく本書の「判断基準」節に置き、
以降の棚卸もこの表を更新する形で行う。

- 測定日: 2026-08-08
- 測定範囲: 2026-05-20 〜 2026-08-07 の会話ログ 1,943 セッション
- 測定ツール: `/ndf:skill-stats`（`plugins/ndf-shared/skills/skill-stats/scripts/skill-stats.py`）

再現手順:

```bash
python3 plugins/ndf-shared/skills/skill-stats/scripts/skill-stats.py \
  --plugin-root plugins/ndf-shared --from 2026-05-20 --to 2026-08-07 --format json
```

## 用語

| 列 | 意味 |
| --- | --- |
| 配布 | 配布先ランタイム。`C` = Claude Code / `X` = Codex / `K` = Kiro。`plugins/ndf-shared/manifests/` の内容 |
| 行数 | `SKILL.md` の行数。運用上限は 500 行 |
| desc | `description` の文字数。運用目標は 300 文字以内 |
| frontmatter 設定 | `明示専用` = `disable-model-invocation: true` / `常時注入` = `user-invocable: false` / `wtu` = `when_to_use` あり / `引数` = `argument-hint` あり / `tools` = `allowed-tools` あり |
| 計 | 起動数の合計（自動 + 明示） |
| 自動 | エージェントが `Skill` ツールで自動起動した回数 |
| 明示 | 利用者がスラッシュコマンドで起動した回数 |
| 機会 | 利用者の発話に Skill の宣言トリガ語が含まれた回数。トリガ語を宣言していない Skill は測定できないため `—` |

## 判断基準

機能が他 Skill と重複するものは、起動数にかかわらず統合の対象とし、内容は統合先へ残す。
統合対象を除いた Skill には、起動数と機会数の 2 軸で次の判定を適用する。

| 起動数 | 機会数 | 既定の判定 | 例外として削除する条件 |
| ---: | ---: | --- | --- |
| 0 | 0 | 削除 | — |
| 0 | 1 以上 | 発動改善 | 手順の中身が現在のモデルの標準能力で足り、Skill 固有の知識が残らないとき |
| 1 以上 | 問わない | 維持 | 起動が 1 回にとどまり、機能が他 Skill または単一のコマンドで代替できるとき |

例外を適用したものは、判定根拠の列に適用した条件を記載する。

## 台帳

| Skill | 配布 | 行数 | desc | frontmatter 設定 | 計 | 自動 | 明示 | 機会 | 判定 | 判定根拠 |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| `branch-fix-strategy` | CXK | 87 | 41 | wtu | 4 | 4 | 0 | 341 | 統合元 | `cherry-pick-pr` とトリガ語が完全重複 |
| `browser-test` | CK | 159 | 37 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 統合元 | ブラウザ自動テストのスクリプト作成と実行工程へ集約 |
| `cherry-pick-pr` | CXK | 120 | 48 | 明示専用 / 引数 / tools | 16 | 1 | 15 | — | 統合先 | `branch-fix-strategy` を吸収。起動 16 回で、実行コマンド側の名前を残す |
| `clean` | CXK | 20 | 40 | 明示専用 / tools | 0 | 0 | 0 | — | 統合元 | 初期実測の機会 251 が `merged` の起動数とほぼ一致し、ブランチ整理は実態として `merged` で行われている |
| `codex` | CK | 473 | 50 | wtu | 4 | 4 | 0 | 51 | 統合元 | 本文の大半が共通。`external-ai` へ統合し、ツール差分を `references/` へ分離 |
| `cross-review` | CXK | 478 | 42 | wtu / 引数 / tools | 286 | 14 | 272 | 1473 | 維持 | 起動 286 回。`description` が 42 文字で発動条件を含まず 254 文字の `when_to_use` に依存しているため、明示トリガの要点を `description` へ移す |
| `data-analyst-export` | — | 64 | 57 | wtu / tools | 0 | 0 | 0 | 54 | 削除 | 例外（モデルの標準能力で足りる）。出力形式の指定のみで Skill 固有の知識が残らない |
| `data-analyst-sql-optimization` | — | 48 | 49 | wtu | 0 | 0 | 0 | 14 | 削除 | 例外（モデルの標準能力で足りる）。機会 14 と少なく、48 行の内容はデータ分析エージェントの定義に直接書ける |
| `deepwiki-transfer` | — | 144 | 45 | 明示専用 / wtu / tools | 1 | 0 | 1 | 0 | 削除 | 例外（起動 1 回・代替あり）。最終利用 2026-05-21。取り込み手順は汎用の取得コマンドで代替できる |
| `deploy` | CXK | 114 | 55 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 発動改善 | 破壊的操作のため明示指示専用を維持。初期実測の機会 340 に対し起動 0 で、`description` の改善と周知が必要 |
| `docker-container-access` | CXK | 76 | 55 | wtu / tools | 6 | 6 | 0 | 33 | 維持 | 起動 6 回 |
| `fix` | CXK | 303 | 34 | wtu / 引数 / tools | 300 | 236 | 64 | 0 | 統合先 | `review-pr-comments` と `resolve-pr-comments` を吸収。起動 300 回で最多 |
| `gemini` | C | 444 | 51 | wtu | 1 | 1 | 0 | 4 | 統合元 | 本文の大半が共通。`external-ai` へ統合し、ツール差分を `references/` へ分離 |
| `git-gh-operations` | CXK | 228 | 44 | wtu / tools | 0 | 0 | 0 | 1840 | 削除 | 例外（モデルの標準能力で足りる）。機会 1,840 は `git add` `git commit` という広すぎるトリガによる誤検出で需要ではない。一般的な Git 操作に Skill 固有の知識が残らない |
| `google-auth` | — | 173 | 29 | wtu / tools | 4 | 4 | 0 | 157 | 維持 | 起動 4 回 |
| `google-chat` | — | 153 | 37 | wtu / tools | 0 | 0 | 0 | 16 | 削除 | 例外（モデルの標準能力で足りる）。機会 16 は通知先の言及にとどまり、手順は汎用の HTTP 呼び出しで代替できる |
| `google-drive` | — | 111 | 55 | wtu / tools | 1 | 1 | 0 | 104 | 維持 | 起動 1 回だが、認証情報の取り回しに Skill 固有の知識がある |
| `implementation-plan` | CXK | 98 | 43 | wtu | 24 | 24 | 0 | 344 | 維持 | 起動 24 回が全数自動起動。現在の `when_to_use` が機能している |
| `investigation-rules` | CXK | 105 | 54 | wtu | 25 | 25 | 0 | 1271 | 維持 | 起動 25 回が全数自動起動。ただしトリガ `調査` が広すぎるため具体化する |
| `issue-plan-strategy` | CXK | 358 | 52 | wtu / 引数 / tools | 141 | 38 | 103 | 142 | 維持 | 起動 141 回 |
| `knowledge-reorg` | — | 269 | 46 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 削除 | 既定（起動 0 / 機会 0） |
| `logging-guidelines` | CXK | 112 | 43 | wtu | 0 | 0 | 0 | 112 | 発動改善 | 機会 112 に対し起動 0。`paths` でコード変更時に限定する |
| `markdown-writing` | CXK | 203 | 76 | wtu / tools | 40 | 21 | 19 | 874 | 維持 | 起動 40 回 |
| `mcp-builder` | — | 236 | 31 | — | 0 | 0 | 0 | — | 削除 | 既定（起動 0 / 機会 0） |
| `merged` | CXK | 29 | 30 | 明示専用 / 引数 / tools | 248 | 0 | 248 | — | 統合先 | `clean` を吸収。起動 248 回。改名しない |
| `ml-model-structure` | — | 152 | 55 | wtu / tools | 2 | 0 | 2 | 94 | 維持 | 起動 2 回。`paths` で `analysis/**` に限定する |
| `ndf-policies` | CXK | 10 | 32 | 常時注入 | 0 | 0 | 0 | — | 維持 | `user-invocable: false` で説明のみを常時注入する設計。自然文からの発動を前提としないため判定対象外 |
| `official-skills-autoloader` | — | 121 | 51 | wtu / tools | 0 | 0 | 0 | 97 | 発動改善 | 機会 97 に対し起動 0。各ランタイムの公式 Skill 提供状況を確認したうえで発動条件を見直す |
| `plan-to-spec` | CXK | 182 | 401 | tools | 0 | 0 | 0 | 2 | 発動改善 | 機会 2 と少なく運用に組み込まれていない。`description` が 401 文字と最長で、要点を残して `when_to_use` へ移す |
| `playwright-browser-connect` | — | 484 | 49 | wtu / tools | 5 | 5 | 0 | 48 | 統合元 | ブラウザ自動テストのスクリプト作成と実行工程へ集約 |
| `playwright-evidence-drive` | — | 190 | 43 | wtu / tools | 0 | 0 | 0 | 3 | 統合元 | ブラウザ自動テストの証跡とレポート工程へ集約 |
| `playwright-execution` | X | 101 | 51 | wtu / tools | 3 | 3 | 0 | 94 | 統合元 | ブラウザ自動テストのスクリプト作成と実行工程へ集約 |
| `playwright-kit-ops` | X | 119 | 56 | wtu / tools | 2 | 2 | 0 | 22 | 維持 | 実行環境ディレクトリとスクリプトを持つため他へ吸収せず単独で残す |
| `playwright-report` | X | 55 | 40 | wtu / tools | 0 | 0 | 0 | 111 | 統合元 | ブラウザ自動テストの証跡とレポート工程へ集約 |
| `playwright-scenario-test` | — | 68 | 52 | wtu / tools | 3 | 0 | 3 | 23 | 統合元 | ブラウザ自動テストのテスト計画工程へ集約 |
| `playwright-script-creation` | X | 108 | 48 | wtu / tools | 0 | 0 | 0 | 16 | 統合元 | ブラウザ自動テストのスクリプト作成と実行工程へ集約 |
| `playwright-test-planning` | X | 97 | 39 | wtu / tools | 1 | 1 | 0 | 233 | 統合元 | ブラウザ自動テストのテスト計画工程へ集約 |
| `pr` | CXK | 161 | 39 | 明示専用 / 引数 / tools | 173 | 2 | 171 | — | 維持 | 起動 173 回。`disable-model-invocation` を外して自然文から発動させる |
| `pr-tests` | CXK | 31 | 38 | 明示専用 / 引数 / tools | 2 | 0 | 2 | — | 維持 | 起動 2 回。`disable-model-invocation` を外して自然文から発動させる |
| `problem-solving` | CXK | 162 | 62 | wtu | 3 | 3 | 0 | 229 | 維持 | 起動 3 回。バグ・障害対応の判断基準として保持 |
| `python-execution` | CXK | 86 | 44 | wtu / tools | 0 | 0 | 0 | 1207 | 削除 | 例外（モデルの標準能力で足りる）。実行環境の検出はモデルが自力で行える。トリガ `python` `スクリプト` が広すぎ他 Skill の発動を埋もれさせている |
| `qa-security-scan` | — | 56 | 34 | wtu | 0 | 0 | 0 | 0 | 発動改善 | 初期実測の機会 66 に対し起動 0。`description` に発動条件を含めて自動発動させる |
| `resolve-pr-comments` | CXK | 146 | 39 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 統合元 | 分類・修正・返信が 3 分割されており、`fix` の一連の流れに含まれる |
| `review` | CXK | 337 | 48 | 明示専用 / 引数 / tools | 58 | 1 | 57 | — | 統合先 | `review-branch` を吸収し `--branch` 引数で切り替える。起動 58 回 |
| `review-branch` | CXK | 129 | 46 | wtu / 引数 / tools | 3 | 3 | 0 | 150 | 統合元 | 対象がローカル差分かの違いのみで、レビュー観点は `review` と同一 |
| `review-pr-comments` | CXK | 110 | 44 | wtu / 引数 / tools | 0 | 0 | 0 | 0 | 統合元 | 分類・修正・返信が 3 分割されており、`fix` の一連の流れに含まれる |
| `skill-stats` | — | 103 | 49 | wtu / tools | 0 | 0 | 0 | 1 | 維持 | 測定ツール自体。自然文からの発動を前提としないため判定対象外 |
| `statusline` | CK | 51 | 47 | 明示専用 / wtu / tools | 3 | 0 | 3 | 16 | 維持 | 起動 3 回 |
| `sync-main` | CXK | 48 | 44 | 明示専用 / tools | 0 | 0 | 0 | — | 削除 | 例外（起動 1 回・代替あり）。Git 操作 1 コマンドに 48 行を割いており `merged` へ吸収できる |

## 判定の内訳

| 判定 | 個数 | 意味 |
| --- | ---: | --- |
| 維持 | 16 | そのまま残す。frontmatter の見直しは行う |
| 統合先 | 4 | 他 Skill を吸収する側 |
| 統合元 | 15 | 統合先へ内容を移して削除する側 |
| 削除 | 9 | 内容ごと削除する |
| 発動改善 | 5 | 残したうえで `description` などの発動条件を見直す |

統合元 15 個は 4 個の統合先と、新設する `external-ai` および 3 個のブラウザ自動テスト
Skill へ集約されるため、統合による減少は 11 個になる。削除 9 個とあわせて 49 → 29 となる。

## 測定ツールの修正

初回の実測は `skill-stats` が使えず個別実装で行った。以降の計測をツールへ一本化するため、
次の 2 点を修正した（[#65](https://github.com/devbasex/ai-plugins/pull/65)）。

| 不具合 | 原因 | 修正 |
| --- | --- | --- |
| 49 個中 48 個でトリガ抽出に失敗し、ヒット率が算出されない | 抽出対象が `description` に限られ、トリガ語を列挙している `when_to_use` を読まなかった。加えて抽出パターンが `Triggers:` 表記に限られ、`明示トリガ:` と書いている `cross-review` を拾えなかった | 抽出対象に `when_to_use` を加え、見出し語として `Triggers:` `明示トリガ:` `トリガ:` を受ける。両フィールドは独立に走査する |
| 利用者の明示起動を数えず、`cross-review` を 14 と報告する | 明示起動は `<command-name>` を含む利用者メッセージとして残るが、これをシステム由来の記述として除外していた | `<command-name>` から明示起動を数え、`Skill` ツール呼び出しと合算して「計 / 自動 / 明示」の 3 列で出力する。トリガ一致の判定からは従来どおり除外する（明示起動は機会ではない） |

修正後にトリガ抽出へ失敗するのは、トリガ語を宣言していない 13 個だけになる。

```text
browser-test, cherry-pick-pr, clean, deploy, knowledge-reorg, mcp-builder,
merged, ndf-policies, pr, pr-tests, resolve-pr-comments, review, sync-main
```

`when_to_use` を持たない Skill は 14 個あるが、そのうち `plan-to-spec` は `description` に
トリガ語を宣言しているため抽出に成功する。

## 初期実測との差異

初期実測（2026-08-07、個別実装）と本台帳（2026-08-08、`skill-stats`）で値が異なる。
差異の要因は 3 つあり、いずれも既知である。

| 要因 | 影響 |
| --- | --- |
| 測定日が 1 日ずれている | 対象セッションが 1,938 → 1,943 件に増え、起動数が数件増減している |
| 機会の判定に使うキーワードが異なる | 初期実測は手書きのキーワード、本台帳は Skill が宣言したトリガ語を使う |
| トリガ語を宣言していない Skill の機会を測れない | 初期実測が値を持っていた `deploy`(340) `clean`(251) `qa-security-scan`(66) は本台帳では `—` になる |

判定に影響する差異は次の 3 件で、いずれも削除の結論は変わらないが、適用する条件が
「既定（起動 0 / 機会 0）」から「例外（モデルの標準能力で足りる）」へ変わる。

| Skill | 初期実測の機会 | 本台帳の機会 | 影響 |
| --- | ---: | ---: | --- |
| `git-gh-operations` | 0 | 1,840 | トリガ `git add` `git commit` がほぼ全セッションに一致する。需要ではなく広すぎるトリガによる誤検出であり、削除の理由は変わらない |
| `google-chat` | 0 | 16 | 通知先の言及にとどまる |
| `data-analyst-sql-optimization` | 0 | 14 | 同上 |

`deploy` と `qa-security-scan` の機会は本台帳では測定できないため、発動改善の判定は初期実測の
値（それぞれ 340 と 66）を根拠とする。両 Skill にトリガ語を宣言したうえで再測定することを、
frontmatter 見直し後の確認項目とする。

## 参照

- 棚卸の計画: [issues/ndf-development-skills/02-skill-inventory.md](../../issues/ndf-development-skills/02-skill-inventory.md)
- frontmatter 規約: [plugins/ndf-shared/skills/README.md](../../plugins/ndf-shared/skills/README.md)
