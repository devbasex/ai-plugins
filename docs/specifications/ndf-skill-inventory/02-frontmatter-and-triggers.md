# NDF Skill 棚卸台帳 — frontmatter とトリガ書式の実測

**この台帳は 3 本に分かれている。**

- [判断基準と台帳](01-ledger-and-criteria.md)
- [frontmatter とトリガ書式の実測](02-frontmatter-and-triggers.md)
- [版ごとの追加と統合](03-version-history.md)

## frontmatter 見直しの結果

[棚卸の計画](../../../issues/old/ndf-development-skills/07-tasks.md) の Task 0-7 で全 29 Skill の
frontmatter を [規約](../../../plugins/ndf/skills/README.md) へ揃えた。台帳の表は測定日
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
