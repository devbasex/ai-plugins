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

| 起動数 | 機会数 | 既定の判定 | 例外として判定を覆す条件 |
| ---: | ---: | --- | --- |
| 0 | 0 | 削除 | 別の測定で需要が確認できるとき、削除せず**発動改善**とする。宣言トリガ語が実際の発話と乖離しているだけで、初期実測など本台帳以外の測定では機会があるものが該当する |
| 0 | 1 以上 | 発動改善 | 手順の中身が現在のモデルの標準能力で足り、Skill 固有の知識が残らないとき、**削除**する |
| 1 以上 | 問わない | 維持 | 起動が 1 回にとどまり、機能が他 Skill または単一のコマンドで代替できるとき、**削除または統合**する |

例外を適用したものは、判定根拠の列に適用した条件を記載する。

機会が `—`（トリガ語を宣言しておらず測定できない）の Skill は、機会数を 0 と断定しない。
起動 0 かつ機会が測定不能なものは、需要を示す実測値が得られていないものとして削除の既定を
準用し、判定根拠の列に「既定を準用」と記載する。準用した既定にも「起動 0 / 機会 0」の行の
例外を同じく適用し、別の測定で需要が確認できるものは削除せず発動改善とする。

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
| `deploy` | CXK | 114 | 55 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 発動改善 | 例外（別の測定で需要を確認）。機会は測定不能だが初期実測の手書きキーワードでは 340 あり、削除の既定を準用せず発動改善とする。破壊的操作のため明示指示専用は維持し、`description` の改善と周知を行う |
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
| `knowledge-reorg` | — | 269 | 46 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 削除 | 既定を準用（起動 0 / 機会は測定不能） |
| `logging-guidelines` | CXK | 112 | 43 | wtu | 0 | 0 | 0 | 112 | 発動改善 | 機会 112 に対し起動 0。`paths` は Claude Code 専用で配布先 3 種のうち Codex/Kiro に効かないため、`description` のトリガ語を `logger` `logging` からログ設計の依頼を表す語へ具体化して 3 ランタイム共通で絞る |
| `markdown-writing` | CXK | 203 | 76 | wtu / tools | 40 | 21 | 19 | 874 | 維持 | 起動 40 回 |
| `mcp-builder` | — | 236 | 31 | — | 0 | 0 | 0 | — | 削除 | 既定を準用（起動 0 / 機会は測定不能） |
| `merged` | CXK | 29 | 30 | 明示専用 / 引数 / tools | 248 | 0 | 248 | — | 統合先 | `clean` を吸収。起動 248 回。改名しない |
| `ml-model-structure` | — | 152 | 55 | wtu / tools | 2 | 0 | 2 | 94 | 維持 | 起動 2 回。`paths` で `analysis/**` に限定する |
| `ndf-policies` | CXK | 10 | 32 | 常時注入 | 0 | 0 | 0 | — | 維持 | Claude Code では `user-invocable: false` により説明のみが常時注入される。Codex / Kiro は同項目を解釈せず通常の Skill として扱うため、Kiro は [Task 0-9](../../issues/ndf-development-skills/07-tasks.md) で `.kiro/steering/` へ移して回避し、Codex は [Task 0-10](../../issues/ndf-development-skills/07-tasks.md) で `description` に「知識として参照する。手順として実行しない」旨を明記する。いずれのランタイムでも自然文からの発動を前提としないため判定対象外 |
| `official-skills-autoloader` | — | 121 | 51 | wtu / tools | 0 | 0 | 0 | 97 | 発動改善 | 機会 97 に対し起動 0。各ランタイムの公式 Skill 提供状況を確認したうえで発動条件を見直す |
| `plan-to-spec` | CXK | 182 | 401 | tools | 0 | 0 | 0 | 2 | 発動改善 | 機会 2 と少なく運用に組み込まれていない。`description` が 401 文字と最長だが、配布が `CXK` で `when_to_use` は Codex/Kiro に効かないため、トリガ語は `description` に残したまま重複した言い換えを削って要約する |
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
| `qa-security-scan` | — | 56 | 34 | wtu | 0 | 0 | 0 | 0 | 発動改善 | 例外（別の測定で需要を確認）。宣言トリガ語での機会は 0 だが、初期実測の手書きキーワードでは 66 あり、宣言トリガ語が実際の発話と乖離している。削除せず、`description` に発動条件を含めたうえでトリガ語を実態へ寄せる |
| `resolve-pr-comments` | CXK | 146 | 39 | 明示専用 / 引数 / tools | 0 | 0 | 0 | — | 統合元 | 分類・修正・返信が 3 分割されており、`fix` の一連の流れに含まれる |
| `review` | CXK | 337 | 48 | 明示専用 / 引数 / tools | 58 | 1 | 57 | — | 統合先 | `review-branch` を吸収し `--branch` 引数で切り替える。起動 58 回 |
| `review-branch` | CXK | 129 | 46 | wtu / 引数 / tools | 3 | 3 | 0 | 150 | 統合元 | 対象がローカル差分かの違いのみで、レビュー観点は `review` と同一 |
| `review-pr-comments` | CXK | 110 | 44 | wtu / 引数 / tools | 0 | 0 | 0 | 0 | 統合元 | 分類・修正・返信が 3 分割されており、`fix` の一連の流れに含まれる |
| `skill-stats` | — | 103 | 49 | wtu / tools | 0 | 0 | 0 | 1 | 維持 | 測定ツール自体。自然文からの発動を前提としないため判定対象外。配布先がなく（`常時注入` も未指定）ランタイム差分は生じない |
| `statusline` | CK | 51 | 47 | 明示専用 / wtu / tools | 3 | 0 | 3 | 16 | 維持 | 起動 3 回 |
| `sync-main` | CXK | 48 | 44 | 明示専用 / tools | 0 | 0 | 0 | — | 削除 | 既定を準用（起動 0 / 機会は測定不能）。Git 操作 1 コマンドに 48 行を割いており `merged` で代替できる |

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
| トリガ語を宣言していない Skill の機会を測れない | 初期実測が値を持っていた `deploy`(340) `clean`(251) は本台帳では `—` になる |

判定に影響する差異は次の 3 件で、いずれも削除の結論は変わらないが、適用する条件が
「既定（起動 0 / 機会 0）」から「例外（モデルの標準能力で足りる）」へ変わる。

| Skill | 初期実測の機会 | 本台帳の機会 | 影響 |
| --- | ---: | ---: | --- |
| `git-gh-operations` | 0 | 1,840 | トリガ `git add` `git commit` がほぼ全セッションに一致する。需要ではなく広すぎるトリガによる誤検出であり、削除の理由は変わらない |
| `google-chat` | 0 | 16 | 通知先の言及にとどまる |
| `data-analyst-sql-optimization` | 0 | 14 | 同上 |

`deploy` はトリガ語を宣言しておらず機会を測定できないため、発動改善の判定は初期実測の値
（340）を根拠とする。

`qa-security-scan` はトリガ語を宣言しており機会 0 と測定できている。初期実測の手書きキーワード
では 66 だったため、この差は宣言トリガ語（`security scan` `OWASP` など）が実際の発話と乖離して
いることを示す。発動改善の判定はこの乖離を根拠とする。

`deploy` へのトリガ語宣言と `qa-security-scan` のトリガ語見直しののち再測定することを、
frontmatter 見直し後の確認項目とする。

## frontmatter 見直しの結果

[棚卸の計画](../../issues/ndf-development-skills/07-tasks.md) の Task 0-7 で全 29 Skill の
frontmatter を [規約](../../plugins/ndf-shared/skills/README.md) へ揃えた。台帳の表は測定日
時点の値であり、以下の変更は表へ反映していない。

### 発動制御

| Skill | 変更 | 理由 |
| --- | --- | --- |
| `merged` / `pr` / `pr-tests` | `disable-model-invocation` を削除 | 日常的に自然文で依頼されるため。明示指示専用のままではエージェントが Skill を使わず独自手順で実行する。実測で 3 個とも自然文から起動することを確認 |
| `review` | `disable-model-invocation` を削除 | 同上。ただし Claude Code では組み込みの `code-review` が同じ用途を持つため自然文では選ばれない。`/ndf:review` の明示起動と `cross-review` からの内部呼び出しは動作する（[#83](https://github.com/devbasex/ai-plugins/issues/83) で追跡） |
| `merged` / `pr` | 実行前確認を必須手順として本文へ固定 | 上記 2 つは取り消しの難しい操作（worktree / ブランチ削除、push と PR 作成）を含む。自動発動を許すかわりに、削除・書き込みの直前に対象を一覧提示して同意を得る手順を `SKILL.md` と `description` に固定した。`disable-model-invocation` を解釈しない Codex / Kiro でも同じ安全性が働く |
| `deploy` / `cherry-pick-pr` / `statusline` | 明示指示専用を維持 | 環境ブランチへの書き込みと設定ファイルの書き換えを伴う。`description` に「利用者が明示的に指示したときのみ実行する」と明記し、Codex / Kiro でも意図が伝わるようにした |
| `ndf-policies` | `user-invocable: false` を維持 | `description` に「知識として参照するだけで、手順として実行しない」と明記した |
| `official-skills-autoloader` | 自動発動を維持し、実行前確認を必須手順として本文へ固定 | 本 PR で Claude Code の manifest へ追加したことで暗黙起動が可能になり、外部リポジトリの clone と `~/.claude/skills/` への symlink 作成が同意なしに走りうる状態になった。明示指示専用（`disable-model-invocation`）も検討したが、この Skill は起動 0 / 機会 97 で台帳の判定が「発動改善」であり、明示専用は判定と逆行して機会 97 をそのまま取りこぼす。また `~/.claude/skills/` を読むのは Claude Code だけで Codex / Kiro には配布しないが、`disable-model-invocation` は Claude Code でも発動制御であって実行前確認ではないため、これだけでは同意取得を保証できない。したがって `merged` / `pr` と同じ「自動発動 + 実行前確認」を採り、クローン元 URL・クローン先・symlink を張る先・対象 Skill 名の 4 点を一覧提示して同意を得る手順を `SKILL.md` と `description` に固定した |

> **v6.0.0 での後続変更**: 上表の `review` は v6.0.0 で **`pr-review` へ改名**した
> （[#83](https://github.com/devbasex/ai-plugins/issues/83)）。自然文発動は
> [#85](https://github.com/devbasex/ai-plugins/pull/85) で追わないと確定しており、改名の目的は
> 明示起動時の識別性である（`review` は `code-review` / `security-review` / `cross-review` の
> 部分文字列で、スラッシュ補完の候補に埋もれる）。上表と以降の集計は **v5.0.0 時点の記録**
> なので旧名のまま残す。

### 配布先

| Skill | 台帳の配布 | 変更後 | 理由 |
| --- | --- | --- | --- |
| `qa-security-scan` | — | CXK | 発動改善の判定はどこにも配布されていない状態では効かない。ランタイム非依存の判断基準であり 3 種すべてへ配る |
| `official-skills-autoloader` | — | C | 同上。ただし取得先が `~/.claude/skills/` のため Claude Code 限定 |

### トリガ語

- 広すぎるトリガを具体化した。`investigation-rules` の `調査` → `調査レポートを書く`、
  `implementation-plan` の `PR作成` を削除して `pr` へ寄せる、`markdown-writing` の
  `仕様書` → `仕様書を書く`、`problem-solving` の `バグ修正` → `バグの根本原因`
- `playwright-evidence` と `playwright-kit-ops` で重複していた `upload_evidence` を解消した。
  スクリプトを持つ `playwright-kit-ops` 側に `upload_evidence.py` として残し、
  `playwright-evidence` は `エビデンスをDriveへ保管` に置き換えた
- `deploy` と `cherry-pick-pr` はトリガ語を宣言していなかったため新たに宣言した。
  次回の `/ndf:skill-stats` で両者の機会を測定できる

### 実測値

| 項目 | 見直し前 | 見直し後 | 上限 |
| --- | ---: | ---: | ---: |
| 検査エラー | 33 | 0 | 0 |
| 検査警告 | 16 | 0 | — |
| `description` 最大 | 401 | 296 | 300 |
| Claude Code 初期一覧 | 3,133 | 6,036 | 8,000 |
| Codex 初期一覧 | 3,933 | 6,473 | 8,000 |
| frontmatter 合計 | 12,724 | 12,211 | 13,000 |

見直し後の値は `python3 scripts/check-skill-frontmatter.py --report` の出力（Skill 29 個、
エラー 0 / 警告 0）である。Claude Code の初期一覧は 1 項目を 250 文字で切り詰めてから積むため、
`description` を 250 文字より長くしても合計は増えない。Codex の初期一覧は Codex の manifest に
載る Skill だけを数えるため、Claude Code 限定の `official-skills-autoloader` は含まれない。

初期一覧の合計が増えているのは、`when_to_use` に置いていたトリガ語を `description` へ移し、
Codex と Kiro でも発動判定に効くようにしたためである。

## v6.1.0 での追加（開発方法論レイヤー）

Skill を 29 個から **34 個**へ増やした。追加した 5 個は起動実績をまだ持たないため、判定は
次回の測定まで保留する。統合・削除の対象ではない。

| Skill | 配布 | 役割 | 判定 |
| --- | --- | --- | --- |
| `development-workflow` | CXK | 変更を 4 モードへ分類し工程へ振り分ける。判定基準の唯一の置き場所 | 未測定 |
| `requirements-design` | CXK | 要求から受け入れ条件と仕様を起こす | 未測定 |
| `tdd-cycle` | CXK | 失敗するテスト → 最小実装 → 整理のサイクル | 未測定 |
| `safe-refactoring` | CXK | コードスメル起点の構造改善と現状固定テスト | 未測定 |
| `quality-gates` | CXK | 完了の定義と、完了宣言前の検証証跡 | 未測定 |

追加にあたり予算を実測しなおした。

| 項目 | v6.0.0（Skill 29 個） | v6.1.0（Skill 34 個） | 上限 |
| --- | ---: | ---: | ---: |
| Claude Code 初期一覧 | 6,919 | 7,772 | 8,000 |
| Codex 初期一覧 | 6,485 | 7,329 | 8,000 |
| frontmatter 合計 | 12,220 | 13,017 | 13,800 |

Claude Code の初期一覧は上限に対する余裕が 228 文字（約 3%）しかない。**次に Skill を追加する
ときは、`description` の合計を先に見積もる必要がある。** v6.1.0 では既存 6 Skill の
`description` から重複したトリガ語（英語表記の言い換え、Skill 名そのもの）を落として 150 文字
分を確保した。frontmatter 合計の運用値は実測 13,017 に約 6% の余裕を足して 13,800 へ更新した。

## トリガ書式の変更の実測

トリガ語の宣言を `Triggers: 'a', 'b'` から `Use when …（a・b）` へ変えても暗黙起動が落ちないかを
実測した（2026-08-13）。対象は起動実績上位の 3 個（`merged` 248 / `pr` 173 / `pr-tests` 2）。

| Skill | 旧書式 | 新書式 | 差 |
| --- | ---: | ---: | ---: |
| `merged` | 241 | 144 | -97 |
| `pr` | 234 | 163 | -71 |
| `pr-tests` | 190 | 141 | -49 |

### Claude Code（claude 2.1.231 / claude-haiku-4-5）

同一の依頼文を、旧書式だけを積んだプラグインと新書式だけを積んだプラグインに対して実行し、
`Skill` ツールの呼び出しを比較した（`--plugin-dir` で probe プラグインを読み込み、
`--settings` で導入済み `ndf@ai-plugins` を無効化）。

| 依頼文 | 旧書式 | 新書式 |
| --- | --- | --- |
| マージ済みのブランチを整理してください。 | `merged` | `merged` |
| この変更で PR を作ってください。 | `pr` | `pr` |
| PRテストを実行してください。 | `pr-tests` | `pr-tests` |

3 件とも一致した。**新書式で起動しなくなった依頼文はない。**

旧書式で宣言していたが新書式の括弧へ入れなかったトリガ語（`draft PR` /
`テスト結果をPRにコメント`）について、その語を含む依頼文でも測った（各 3 回）。

| 依頼文 | 旧書式 | 新書式 |
| --- | --- | --- |
| draft PR を作ってください。 | 2/3 | **3/3** |
| テスト結果を PR にコメントしてください。 | **0/3** | **3/3** |
| レビュー用にこの作業を push してください。 | 1/1 | 1/1 |

`テスト結果をPRにコメント` は旧書式で**宣言トリガ語だったにもかかわらず 3 回とも起動しなかった**。
`Triggers:` の列挙は `description` の末尾にあり、Claude Code の 250 文字での切り詰めと、
判定が文全体の意味に依存することの両方で不利に働くと考えられる。この 2 語は新書式でも
`Use when` の条件文と括弧へ入れ直したうえで測っている。

したがって書式変更は**短くなるだけでなく、末尾に積んだトリガ語より発動が安定する**。
ただし 1 依頼文あたり 3 サンプルの測定であり、統計的な差の主張はしない。

### Codex（codex-cli 0.147.0、2026-08-14 に単独条件で再測定）

旧書式・新書式それぞれ 3 Skill だけを持つ probe プラグインを一時的なローカルマーケットプレイス
から導入し、**導入済みの `ndf@ai-plugins` を無効化した単独条件**で測定した。

| 依頼文 | 旧書式 | 新書式 |
| --- | --- | --- |
| マージ済みのブランチを整理してください。 | `merged` | `merged` |
| この変更で PR を作ってください。 | `pr` | `pr` |
| テスト結果を PR にコメントしてください。 | `pr-tests` | `pr-tests` |

`description` をそのまま引用させると、旧環境は `Triggers: 'マージ後の後片付け', …` を含む旧文面、
新環境は `… Use when a PR was merged（マージ後の後片付け・ブランチを整理・worktreeを削除）.` を
返した。**各環境が自分の書式を初期一覧として読み込んでいる**ことの確認である。

Claude Code で旧書式が落ちた「テスト結果を PR にコメントしてください。」は、Codex では旧書式でも
起動した。末尾の列挙の届き方はランタイムによって違う。

初回の測定（codex-cli 0.146.1）では、導入済みの旧書式が同時に一覧へ載っていたため新旧の分離が
できず、単独条件の測定は `codex exec` が応答せず未完だった。CLI の更新と再ログインで解消した。

### Kiro（kiro-cli 2.16.1、2026-08-14 に単独条件で再測定）

旧書式（v6.1.0）と新書式それぞれの `install.sh` を別プロジェクトへ実行し、
`kiro-cli chat --no-interactive --agent ndf` で測定した。

| 依頼文 | 旧書式 | 新書式 |
| --- | --- | --- |
| マージ済みのブランチを整理してください。 | `merged` | `merged` |
| この変更で PR を作ってください。 | `pr` | `pr` |
| PRテストを実行してください。 | `pr-tests` | `pr-tests` |
| テスト結果を PR にコメントしてください。 | `pr-tests` | `pr-tests` |

`description` の引用でも、旧環境は旧文面・新環境は新文面を返した。Codex と同じく、Claude Code で
旧書式が落ちた依頼文でも Kiro では旧書式が起動している。

初回は `kiro-cli` が未認証で測定不能だった。再ログインで解消した。

### 3 ランタイムの結論

**新書式で起動しなくなった依頼文は 3 ランタイムいずれにも無い。** Claude Code では新書式の方が
安定して起動し（`テスト結果をPRにコメント` が 0/3 → 3/3）、Codex と Kiro では新旧同等だった。

### 全 Skill へ広げたあとの抜き取り実測

30 個を新書式へ書き換えたあと、5 Skill を抜き取って同じ方法で測った（Claude Code / haiku）。

| 依頼文 | 旧書式 | 新書式 |
| --- | --- | --- |
| PRのコメントに対応してください。 | 起動なし | `fix` |
| 実装プランを作ってください。 | `implementation-plan` | `implementation-plan` |
| 仕様書を書いてください。 | 0/2 | 2/3（下記の修正後） |
| このバグの根本原因を調べてください。 | 起動なし | 起動なし |
| セキュリティレビューをしてください。 | 組み込みの `security-review` | 組み込みの `security-review` |

`markdown-writing` は最初の圧縮案で**退行した**。用途文から `Markdown` を後方へ移し、
`authoring or editing a Markdown document` を `authoring Markdown` へ縮めたことが原因で、
括弧内にトリガ語 `仕様書を書く` があっても届かなかった。用途文を戻して解消している。
**トリガ語を括弧へ入れれば用途文を削ってよい、とは言えない。** 用途文の主語（何についての
Skill か）は残す必要がある。

`problem-solving` は新旧とも起動しない。圧縮による退行ではなく、v6.0.0 以前からの状態である
（台帳の起動 3 回はすべて自動起動だが、この依頼文では届かない）。`qa-security-scan` は
Claude Code 組み込みの `security-review` が優先される。いずれも本変更とは別の課題として残る。

### 圧縮の効果（Skill 34 個）

| 指標 | 圧縮前 | 圧縮後 |
| --- | ---: | ---: |
| `description` 合計 | 8,044 | 5,102 |
| 1 個あたり平均 | 237 | **150** |
| 最大 | 296 | 207 |
| Claude Code 初期一覧 | 7,772 | 5,845 |
| Codex 初期一覧 | 7,329 | 5,433 |

### 残るリスク

3 ランタイムとも単独条件で実測し、新書式による退行は見つからなかった。ただし測定した依頼文は
限られる。

| 測定 | 依頼文 | 対象 Skill |
| --- | ---: | --- |
| Claude Code（書式の A/B） | 6 種 | 起動実績上位の 3 個（`merged` / `pr` / `pr-tests`） |
| Claude Code（全 Skill 圧縮後の抜き取り） | 5 種 | `fix` / `markdown-writing` / `implementation-plan` / `problem-solving` / `qa-security-scan` |
| Codex | 3 種 | 上位 3 個 |
| Kiro | 4 種 | 上位 3 個 |

残り 22 個の Skill は依頼文での実測をしていない。他の Skill で起動しないものが見つかった場合は、
その Skill の**用途文**を見直す（旧書式へ戻す道は残していない。`Triggers:` は廃止し、残っていると
検査が失敗する）。実測では、旧書式で宣言していたトリガ語が届かず新書式で届いた例があり、
戻すことが解決になるとは限らない。

## v7.0.0 での圧縮と分割

Skill の `name` と `description` は起動時の一覧として常時注入され、その予算は**プラグイン
横断で共有される**。この開発環境の実測（2026-08-13）では公式プラグイン 35 Skill = 7,903 文字 +
NDF 30 Skill = 6,582 文字 = 14,485 文字がすべて一覧に載っており、規約に書いた `8,000` は
「コンテキスト長が不明なときのフォールバック値」として働いていた。したがって目的は
「上限に収める」ことではなく「取り分を下げる」ことである。

### 施策と効果

| 施策 | 内容 |
| --- | --- |
| トリガ書式の変更 | `Triggers: 'a', 'b'` を廃止し `Use when …（a・b）` へ。旧書式は検査で失敗させる |
| `description` の圧縮 | 全 Skill を新書式へ。用途文も冗長な言い換えを削る |
| playwright 系の分離 | 4 Skill を `playwright-kit` プラグインへ。Skill 名は変えない |
| `allowed-tools` | **削らない**。利用制限ではなく事前承認で、外すと手順のたびに承認を求められる |

| 指標 | v6.1.0 | v7.0.0（ndf 単独） | v7.0.0（全 family 合計） |
| --- | ---: | ---: | ---: |
| Skill 数 | 34 | 30 | 34 |
| `description` の 1 個あたり平均 | 237 | 148 | 150 |
| Claude Code 初期一覧 | 7,772 | **4,990** | 5,807 |
| Codex 初期一覧 | 7,329 | 4,578 | 5,395 |
| frontmatter 合計 | 13,017 | 7,578 | **10,559** |

frontmatter 合計の運用値は family をまたいだ合計として 11,200 文字へ再設定した。

### 他プラグインとの比較（2026-08-13 実測）

| プラグイン | Skill 数 | `description` 合計 | 1 個あたり平均 | frontmatter 合計 |
| --- | ---: | ---: | ---: | ---: |
| superpowers | 28 | 3,722 | 132 | 4,868 |
| slack | 13 | 5,219 | 401 | 5,940 |
| notion | 4 | 1,029 | 257 | 1,218 |
| **ndf（v6.1.0）** | 34 | 8,044 | 237 | 13,051 |
| **ndf（v7.0.0）** | 30 | 4,463 | 148 | 7,578 |

superpowers は 28 個で NDF と同規模ながら frontmatter が 1/2.7 だった。書き方は
`description` が `Use when …` の 1 文だけで、トリガ語の列挙・引用符・追加フィールドを持たない。
v7.0.0 の書式はこの方針に寄せたものである。

## v8.0.0 での統合と改名（refactoring）

`safe-refactoring` を **`refactoring`** へ改名し、分岐・反復・定数の表現を決める観点を統合した。
Skill 数は 30 個で変わらない。

統合の根拠は本書「判断基準」節の「機能が他 Skill と重複するものは、統合の対象とし、内容は
統合先へ残す」である。観点を独立 Skill として置くと、発動条件が「コードを書くとき」以外に
書けず、常に該当するトリガは発動判定として働かない。構造改善の起点である
`safe-refactoring` の観点へ寄せることで、発動点が「リファクタリングを始めるとき」に定まる。

| Skill | 配布 | 変更 | 判定 |
| --- | --- | --- | --- |
| `refactoring` | CXK | `safe-refactoring` から改名。コードスメルに 3 件追加、参照資料を 2 件追加 | 改名前の実測を引き継ぐ |

追加した観点と参照資料:

| 追加物 | 内容 |
| --- | --- |
| コードスメル 3 件 | 業務ルールの埋め込み / 一件ずつの反復 / 検証のない外部化 |
| 手法 2 件 | 対応表への置き換え / 一括処理への置き換え |
| `references/data-representation.md` | 分岐・反復・定数の判定表、外部化してよい条件、判断の記録 |
| `references/language-notes.md` | Python / JavaScript / TypeScript / PHP での手段 |

改名は公開コマンドの非互換変更にあたるため、対応表を `ndf-policies` へ置いた（v9.0.0 で削除）。
あわせて v7.0.0 の対応表（playwright 系の分離）を予告どおり削除した。

予算への影響（`python3 scripts/check-skill-frontmatter.py --report`）:

| 指標 | v7.0.0 | v8.0.0 | 運用値 |
| --- | ---: | ---: | ---: |
| Skill 数（ndf 単独） | 30 | 30 | — |
| Claude Code 初期一覧 | 5,807 | 5,855 | 8,000 |
| Codex 初期一覧 | 5,395 | 5,443 | 8,000 |
| frontmatter 合計 | 10,559 | 10,612 | 11,200 |

## 参照

- 棚卸の計画: [issues/ndf-development-skills/02-skill-inventory.md](../../issues/ndf-development-skills/02-skill-inventory.md)
- frontmatter 規約: [plugins/ndf-shared/skills/README.md](../../plugins/ndf-shared/skills/README.md)
