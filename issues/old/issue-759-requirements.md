# #759: cross-refactoring / cross-review: --model を指定しないと実際に動いたモデルが記録されず、モデル別に集計できない

正は課題の本文（#759）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文をそのまま引用する（2026-10-02 に要求と受け入れ条件へ書き直す前の全文）。

> ## 何が起きているか
>
> `cross-refactoring` を rf677 / 683 / 717 / 746 / 747 / 749 / 751 / 752 / 757 の 9 回走らせた後、実装担当を「ランタイム × モデル」ごとに集計しようとした。**集計できたのはランタイムの単位までで、モデルの単位は 1 件も取れなかった。**
>
> rf751 で `refactor.py report 751 --metrics` を実行すると、実装担当の表は 1 行しか出ず、それ以外のラウンドはすべて「集計から分離したラウンド」へ回された。
>
> ```text
> | ランタイム / モデル | 担当R | 適用 | 見送り | ...
> | claude / default | 1 | 5 | 0 | ...
>
> - round 1: codex はモデルを指定しておらず、実際に動いたモデルも取得できないため、実装担当の集計から分離する
> - round 3: kiro の auto はラウンドごとに違うモデルが動きうるため、実装担当の集計から分離する
> ...（17 行）
> ```
>
> 原因は、実行したモデルの記録を `--model` の指定値に頼っていることである。9 回とも `--model` を指定しておらず、状態ファイルの `models` は 4 者とも `null` だった。**指定値に頼る限り、指定し忘れた過去の実行は後から比べられない。** 既定値のまま運転する日常の実行ほど、記録が残らない。
>
> ## 何を求めるか
>
> `--model` を指定しなくても、**実際に動いたモデル**を 4 つのランタイムについて記録する。対象は `cross-refactoring` の実装担当と、`cross-review` / `cross-refactoring` のレビュー担当（`reviewer_models`）である。
>
> ## 手がかり（2026-09-18 に手元で実測）
>
> #284 は「CLI の構造化出力にモデル名が載らない」として、**実測できないことを記録に残す方針**で閉じた。ただし、構造化出力の外側（セッションの記録・ログ）には載っているランタイムがある。
>
> | ランタイム | 版 | どこに載るか | 実測した値 | 取れるか |
> | --- | --- | --- | --- | --- |
> | claude | — | `--output-format json` の `modelUsage`（既存） | `claude-opus-5[1m]` と `claude-haiku-4-5-20251001` の 2 つ | 取れる。**ただし選び方に不具合がある**（下記） |
> | codex | codex-cli 0.153.4 | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` の `"model"` | `gpt-5.6-sol`（`~/.codex/config.toml` の `model` と一致） | 取れる見込み。起動とセッションの記録を結びつける方法が要る |
> | agy | 1.2.6 | `~/.gemini/antigravity-cli/cli.log` の要求 URL `models/<名前>:streamGenerateContent` | `gemini-3.8-flash`（**`~/.gemini/settings.json` の `gemini-3.1-pro-preview` とは異なる**） | 取れる見込み。**設定ファイルでは代用できない**。起動の時刻範囲でログを切り出す必要がある |
> | kiro | kiro-cli 2.21.1 | `~/.kiro/sessions/cli/*.json(l)` の `model_id` / `modelId` | `auto` だけ。auto が実際に選んだモデル名は見つからなかった | 未確認。他の出力・ログを調べる必要がある |
>
> ## 既存の実測の不具合（同時に直す）
>
> **claude の実測値が補助のモデルになっている。** rf751 の `claude-apply-r1-stdout.log` の `modelUsage` は次のとおりだった。
>
> | モデル | `inputTokens` | `cacheReadInputTokens` | `outputTokens` | `costUSD` |
> | --- | ---: | ---: | ---: | ---: |
> | `claude-haiku-4-5-20251001` | 3,944 | 0 | 28 | 0.004 |
> | `claude-opus-5[1m]` | 22 | 639,525 | 4,377 | 1.012 |
>
> `observed_model()`（`plugins/ndf/scripts/lib/models.py:137`）は `inputTokens` が最大のものを主たるモデルとする。キャッシュ読み取りを数えないため、補助で動いた haiku を選ぶ。主に動いたのは opus である。
>
> **実装担当は 1 回の実行につき 1 者である。** `cross-refactoring` は `--implementer` → ホスト → 参加者の先頭で実装担当を 1 者に決め、
> 実測値は状態ファイルの `implementer_model.observed` に 1 つだけ書く（`refactor_lib/results.py:50` の `record_observed_model`）。
> `external-ai.py run` も同じ `models.observed_model` を呼び、取れなければ結果の `model` に指定値か `default` を入れる
> （`external-ai/scripts/external-ai.py:207-210`）。
>
> ## どこで取るか
>
> **実測したモデルの取得は起動層の責務にする。** `external-ai.py run` は結果 JSON に `"model"` を返す
> （`{"status":...,"runtime":"codex","model":"..."}`）。codex / agy / kiro のセッションの記録から取る処理は
> `plugins/ndf/scripts/lib/models.py` の `observed_model` に置き、`external-ai.py` と `cross-refactoring` の `results.py` の両方がそれを読む。
>
> claude の選び方の不具合（`models.py:155-160` が `inputTokens` だけで主たるモデルを選ぶ）は、この課題で直す。
>
> ## 受け入れ条件
>
> 実装担当は 1 回の実行につき 1 者で、計画の 1 回の実行が 1 者の実装担当を持つ（https://github.com/devbasex/ai-plugins/issues/933 の後の `cross-refactoring`）。実測値は状態ファイルの計画の実装担当の欄（`implementer_model.observed`）に 1 つ入る。
>
> - [ ] `--model` を指定しない実行で、実装担当が codex か agy のとき、計画の実装担当の欄（`implementer_model.observed`）に実測のモデル名が入る
> - [ ] kiro については、auto が選んだモデルを取れるかを調べて結論を記録する。取れないなら理由と調べた場所を残す
> - [ ] 実装担当が claude のとき、計画の実装担当の欄に入る実測値が、主に動いたモデル（上の例では `claude-opus-5`）になる
> - [ ] 実測値がある実行は、`--model` を指定していなくても `report --metrics` の集計に入る
> - [ ] 別の実行の起動が並行しても、別の起動の記録を拾わない（セッション ID・時刻範囲・作業ディレクトリのいずれかで結びつける）
> - [ ] `SKILL.md` と `CLAUDE.md` の「実際に動いたモデルを取得できるのは claude だけ」「`--model` を 4 つとも指定する」の記述を、実装後の事実に合わせる
>
> ## 関連
>
> - #284（実測できるのは claude だけ、として閉じた課題）
> - #852（closed） — `external-ai.py run` の結果 JSON が `model` を返す
> - #870（closed） — cross-review 側の `models.py` / `metrics.py` をライブラリへ移した
> - #760 — worker の輪番と統計の鍵が「ランタイム × モデル」。この記録が前提になる
> - 集計の元にした記録: `~/.local/state/ndf/metrics/devbasex--ai-plugins/cross-refactoring-rf*.json`、状態ファイル `cross-refactoring-rf{677,751,757}-state.json`
>

## 目的

- `--model` を指定しない日常の実行でも、**実際に動いたモデル**を「ランタイム × モデル」の単位で記録し、後からモデル別に比べられるようにする（#760 の輪番と統計の鍵の前提）
- claude の実測値が補助のモデル（haiku）を指す不具合を直し、主に動いたモデルを記録する

## 前提

- 前提 1: 実測値の取得は起動層（`plugins/ndf/scripts/lib/models.py` の `observed_model` と、その呼び出し元の `external-ai.py run` / `cross-refactoring` の `results.py` / `cross-review` のレビュー担当の起動）に置く。呼び出し側ごとに別の取得処理を持たない（Value 6）
- 前提 2: codex は `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` の `session_meta`（`session_id` / `cwd` / `timestamp`）と `"model"` で取れる。2026-10-02 に codex-cli 0.159.3 で `session_meta` に `cwd` と `timestamp`、同じファイルに `"model":"gpt-6.1-sol"` があることを確かめた
- 前提 3: agy の要求ログ（課題の実測では agy 1.2.6 の `~/.gemini/antigravity-cli/cli.log`）は、2026-10-02 の手元（agy 1.2.11）には存在しなかった。**記録の置き場は設計の工程で今の版について測り直す。** 測り直しても公開された置き場が見つからなければ、agy は「取れない」と結論して理由と調べた場所を残す（受け入れ条件 2 と同じ扱い）
- 前提 4: kiro は 2026-10-02 に kiro-cli 2.24.1 の `~/.kiro/sessions/cli/*.jsonl` を見て `"modelId":"auto"` しか無かった。auto が選んだモデルは、ほかの出力・ログ（`~/.kiro/logs` など）を設計の工程で調べて結論を出す
- 前提 5: claude の「主に動いたモデル」は、`modelUsage` の各モデルについて `inputTokens` + `cacheReadInputTokens` + `cacheCreationInputTokens` + `outputTokens` の和が最大のものとする。課題の例では opus が 643,924、haiku が 3,972 で、opus を選ぶ。和でなく `costUSD` で選ぶほうが良いと設計が判断したら、設計の決定の記録に理由を書いて差し替えてよい（どちらでも例では opus になる）
- 前提 6: 記録する値は CLI が出した名前そのままとする（claude なら `claude-opus-5[1m]`）。課題の受け入れ条件の「`claude-opus-5`」は opus を指す例示として読み、`[1m]` のような接尾辞は落とさない。落とすと 1M コンテキストの版と通常の版を区別できず、指定値との突き合わせ（`mismatch_warning`）も CLI の名前と一致しなくなる
- 前提 7: 実行とセッションの記録の結びつけは、**起動の開始〜終了の時刻範囲と作業ディレクトリの両方**で行う（ランタイムがセッション ID を起動側へ返せるならそれを優先する）。範囲内に候補が 2 件以上あって 1 つに絞れないときは「取れない」として `null` を残す。誤った値を実測として記録するより、取れないほうが害が小さい（`models.py` の `OBSERVABLE_RUNTIMES` の注記と同じ判断）
- 前提 8: #933 の後の `cross-refactoring` の状態ファイルはラウンドを持たず、実装担当の実測値は最上位の `implementer_model.observed` 1 つである。一方 `plugins/ndf/scripts/lib/metrics.py` の `aggregate` はラウンドの `impl_model` / `reviewer_models` を読み、本番のコードからは呼ばれていない（2026-10-02 に `grep -rn "metrics.aggregate\|import metrics"` で確認）。また `report --metrics` は今は「種類別の件数と所要」だけを出す。**課題の言う「`report --metrics` の集計」は、実行の要約（`~/.local/state/ndf/metrics/<repo>/cross-refactoring-rf*.json`）と `report` の出力を指すものとして読む**
- 前提 9: `cross-review` のレビュー担当の起動（`launch-reviewer.sh` / `critique.sh`）は今は `--model` を渡さず、状態ファイルに `reviewer_models` を書いていない（2026-10-02 に `grep -rn reviewer_models plugins/ndf` で確認。読むのは `metrics.py` とテストだけ）。この課題ではレビュー担当の実測値を記録する欄を足す。欄の名前と位置は設計が決める

## 対象範囲

含む:
- codex / agy / kiro のセッションの記録から、実際に動いたモデルを取る処理（`models.py`）。kiro と agy は取れない結論もありうる
- claude の主たるモデルの選び方の修正
- `cross-refactoring` の実装担当の実測値（`implementer_model.observed`）と、実行の要約・`report` への反映
- `cross-review` と `cross-refactoring` のレビュー担当の実測値の記録
- `external-ai.py run` の結果 JSON の `model` に、取れた実測値が入ること
- 分離の判定（`separation_reason` / `is_measurable` / `assumption_note`）が実測値の有無を見るようにすること
- `SKILL.md` と `external-ai` の references の「実測できるのは claude だけ」「`--model` を全員に指定する」の記述の更新

含まない:
- 過去の実行（rf677〜rf757 など）の状態ファイル・要約を遡って埋め直すこと
- ラウンドの形を前提にした `metrics.aggregate` の作り直し・削除（本番から呼ばれていない件は別の課題にする）
- #760 の輪番と統計の仕組みそのもの
- `--model` の指定方法の変更、モデル名の綴りのチェック
- ランタイムの設定ファイル（`~/.gemini/settings.json` など）からの代用。課題の実測で agy は設定と実際が食い違ったため採らない

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | CLI を起動した（開始時刻と作業ディレクトリが決まった） | 実装担当・レビュー担当・`external-ai.py run` の起動 | 起動の失敗は既存の監視が扱う。実測は行わない | — |
| E2 | CLI が終わった（終了時刻が決まった） | CLI の終了・監視による停止 | 監視が止めた場合も E3 へ進む（途中まで動いたモデルも記録の対象） | E1 |
| E3 | ランタイムのセッションの記録から、E1〜E2 の範囲と作業ディレクトリに合う 1 件を選んだ | E2 | 記録が無い・読めない・形が変わった・候補が 2 件以上 → 実測値は `null` のまま。処理は止めない | E2 |
| E4 | 実際に動いたモデル名を取り出した | E3 | 名前の欄が無い（kiro の `auto` だけなど）→ `null` | E3 |
| E5 | 実測値を状態ファイル（実装担当は `implementer_model.observed`、レビュー担当は設計が決める欄）と結果 JSON に書いた | E4 | 書き込みの失敗は既存の状態ファイルの保存と同じ扱い | E4 |
| E6 | 指定値と実測値を突き合わせ、食い違えば警告した | E5 | 指定が無ければ突き合わせない | E5 |
| E7 | 実行の要約と `report` に、実測値と分離の判定を出した | `report` の実行・状態の保存 | 実測値が `null` で指定も無いときだけ、従来どおり分離の理由を出す | E5 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 実測値 | CLI の出力・セッションの記録から取った、実際に動いたモデル名（状態ファイルの `observed`） |
| 指定値 | `--model` で渡したモデル名（状態ファイルの `requested`）。未指定なら `null` |
| 主たるモデル | 1 回の起動で複数のモデルが動いたとき、記録する 1 つ（前提 5 の選び方） |
| 分離 | 何が動いたか分からない実行を、モデル別の集計に入れず理由だけを報告すること |

## 受け入れ条件

実装担当は 1 回の実行につき 1 者で、計画の 1 回の実行が 1 者の実装担当を持つ（#933 の後の `cross-refactoring`）。実測値は状態ファイルの計画の実装担当の欄（`implementer_model.observed`）に 1 つ入る。

- [ ] 1. `--model` を指定しない `cross-refactoring` の実行で、実装担当が codex のとき、`implementer_model.observed` にセッションの記録の `"model"` の値（例: `gpt-6.1-sol`）が入る
- [ ] 2. agy と kiro について、実際に動いたモデルを取れるかを今の版（agy 1.2.11 以降・kiro-cli 2.24.1 以降）で調べ、結論を設計文書に記録する。取れるなら 1 と同じく `implementer_model.observed` に入る。取れないなら、理由・調べた場所・調べた版を残す
- [ ] 3. 実装担当が claude で `modelUsage` が課題の例（haiku: input 3,944 / output 28、opus: input 22 / cacheRead 639,525 / output 4,377）のとき、`implementer_model.observed` は `claude-opus-5[1m]` になる（`claude-haiku-4-5-20251001` にならない）
- [ ] 4. 実測値がある実行は、`--model` を指定していなくても分離されない。`separation_reason` は `None` を返し、`report` の実装担当の行と実行の要約（`cross-refactoring-rf*.json`）に実測のモデル名が出る
- [ ] 5. 実測値が取れず `--model` も無い実行は、従来どおり分離の理由（ランタイムごとの文言）が出る
- [ ] 6. 同じ時刻範囲に、別の作業ディレクトリで同じランタイムの起動が並行していても、別の起動のセッションの記録を拾わない。候補が 1 つに絞れないときは `null` が入る（テストで 2 つのセッションの記録を並べて確かめる）
- [ ] 7. `external-ai.py run` を `--model` なしで codex に対して実行すると、結果 JSON の `model` に実測のモデル名が入る（`default` にならない）
- [ ] 8. `cross-review` のレビュー担当（codex / claude、取れれば agy / kiro）について、実測のモデル名が状態ファイルのレビュー担当ごとの欄に入る
- [ ] 9. 指定値と実測値が食い違うとき、既存の警告（`mismatch_warning`）が codex でも出る
- [ ] 10. セッションの記録が無い・読めない・想定と形が違うとき、実行は失敗せず、実測値が `null` のまま進む
- [ ] 11. `cross-refactoring` の `SKILL.md`、`external-ai` の `references/cli-claude.md` / `cli-agy.md`（と該当すれば `cli-codex.md` / `cli-kiro.md`）、`models.py` の注記にある「実際に動いたモデルを取得できるのは claude だけ」「`--model` を全員に指定する」の記述が、実装後の事実（取れるランタイムと取れないランタイム）と一致する
- [ ] 12. 既存のテスト（`plugins/ndf/scripts/tests` と `plugins/ndf/skills/*/tests`）がすべて通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | セッションの記録を探す範囲は、起動の日付のディレクトリ（codex は `YYYY/MM/DD`。日をまたぐ起動は前後 1 日）と時刻範囲に限る。ホーム配下の全セッションを毎回走査しない |
| 運用・保守性 | 実測値を取れなかった理由（記録が無い・候補が複数・名前の欄が無い）を、状態ファイルか標準エラーに 1 行で残し、後から切り分けられるようにする |
| セキュリティ | セッションの記録から読むのはモデル名・セッション ID・作業ディレクトリ・時刻だけで、会話の本文や認証情報を状態ファイル・要約・ログへ写さない（C1）。ランタイムの記録と設定は読むだけで書き換えない（C6） |
| システム環境 | 記録の置き場のパスは、ランタイムの既定の場所を既定値にし、環境変数（`CODEX_HOME` など、ランタイムが持つもの）があればそれに従う。ai-plugins の環境に固有のパスを埋め込まない（Value 5） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `external-ai.py run` の結果 JSON の `model` の値が、codex（と取れれば agy / kiro）で `default` から実測値へ変わる。欄の形は変わらない |
| データ | `cross-refactoring` の状態ファイルの `implementer_model.observed` に値が入るようになる。`cross-review` の状態ファイルにレビュー担当の実測値の欄が増える。実行の要約に実装担当のモデルが増える。過去のファイルの移行はしない（欄が無ければ `null` として読む） |
| 既存の振る舞い | claude の実測値が変わる（補助のモデル → 主たるモデル）。そのため、指定値が opus のときに出ていた誤った食い違いの警告が出なくなる。分離されていた codex の実行が集計に入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` |
| 手動確認 | 実装担当を codex にした `cross-refactoring` を `--model` なしで 1 回走らせ、状態ファイルの `implementer_model.observed` と `report` の実装担当の行を見る。`external-ai.py run` を codex に `--model` なしで 1 回走らせ、結果 JSON の `model` を見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 取得の処理はライブラリの `plugins/ndf/scripts/lib/models.py` に置く。cross-review と cross-refactoring で同じ役割の関数を分けない（`CLAUDE.md`・Value 6）。実験版の置き場（`scripts/experimental/`）には置かない（既定で働く振る舞いのため安定版） |
| コーディング規約 | `AGENTS.md` のベストプラクティス。外部の記録の形は、書く前に今の版で実物を開いて確かめる |
| テスト戦略 | セッションの記録の形を写した小さな固定データで、取り出し・結びつけ・並行時の取り違え防止・形が違うときの `null` を単体テストで担保する。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、取れないときに `null` を残すこと、取れなかった理由の記録 |
| 確認してから行う | ランタイムの記録の非公開の形（SQLite・内部ログ）に依存する取得を入れること。版が上がると誤った値を実測として記録するおそれがあるため、設計で根拠を示す |
| 行わない | ランタイムの設定ファイルからの代用、利用者のホーム配下の記録の書き換え・削除、過去の実行の記録の書き直し |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| agy の今の版での記録の置き場と、取れるかどうか（前提 3） | 設計の工程（AI が実測して決める） | 設計 PR まで |
| kiro の auto が選んだモデルを取れるか（前提 4・受け入れ条件 2） | 設計の工程（AI が実測して決める） | 設計 PR まで |
| 主たるモデルの選び方をトークンの和にするか `costUSD` にするか（前提 5） | 設計の工程 | 設計 PR まで |
| `cross-review` の状態ファイルでレビュー担当の実測値を置く欄の名前と位置（前提 9） | 設計の工程 | 設計 PR まで |
| 本番から呼ばれていない `metrics.aggregate` の扱い（前提 8） | 別の課題として起票し、棚卸しで決める | — |
