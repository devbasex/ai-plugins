# 棚卸しを工程表の行にし、1 回の件数と書き込む先に上限を置く

課題の棚卸し（`backlog-refinement`）を、工程表の最後の行 `棚卸し` として通す。1 回に区分を決める
候補の数は上限で切り、反映は記録のリポジトリの外へ書かない。3 層の supervisor は、フェーズの終わりの
条件を満たしたら Skill の本文の続きを始めない。この文書は、スクリプトの契約と、それぞれをそう決めた
理由を残す。

**手順は各 Skill の文書が正である。** 工程表・フェーズの表・手順・報告の形をここへ書き写さない。

| 何を読むか | 正本 |
| --- | --- |
| 工程表の `棚卸し` の行 | `plugins/ndf/skills/development-workflow/SKILL.md` の「モードごとに起動する Skill」 |
| 棚卸しを通すフェーズ（仕上げ）と終わりの条件、`light` で仕上げを作らないときの置き場、supervisor の規則 14 | `plugins/ndf/skills/development-workflow/references/agent-layers.md` |
| `pace: fast` でスプリントの終わりのプランへ移すこと | `plugins/ndf/skills/development-workflow/references/pace.md` |
| 「スプリントを閉じる → 棚卸し」の順序 | `plugins/ndf/skills/progress-tracking/SKILL.md` の「スプリントを閉じる」 |
| 候補の上限と持ち越し、書き込む先の限定、無人の経路の終了コード 10、完了報告の「持ち越し」 | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 宣言 `.ndf/backlog.json` の `candidate_limit` | `plugins/ndf/skills/backlog-refinement/references/ranking.md` の「宣言の書き方」 |
| 親 issue の起票と結び付けの書き込む先 | `plugins/ndf/skills/backlog-refinement/references/grouping.md` |

## 概要

**例（devbase v3.7.0 の仕上げ、2026-09-23）。** 振り返りの記録を投稿した後に、担当が `retrospective` の
本文の指示に従って open 21 件すべての棚卸しへ入り、他のリポジトリの課題 4 件の本文を書き換えたところで
conductor が打ち切った。今の形では次のように動く。

| 時点 | 振る舞い |
| --- | --- |
| 振り返りの記録を投稿した | `retrospective` は棚卸しを指示しない。フェーズの表の「通す工程」の次が `棚卸し` である |
| スプリントを閉じた（`sprint-close.py` が `ok`） | 仕上げの担当が進捗記録 `stage 棚卸し` を打つ。`stopped` なら棚卸しへ進まない |
| 候補を集める | `upkeep.py candidates` が上限 10 件で切る。残る 11 件は `metrics.deferred` に番号だけが載る（終了コード 20） |
| 反映する | `upkeep.py apply` は記録のリポジトリ（devbasex/devbase）の課題とマイルストーンにだけ書く |
| 「やらない」の承認が要る（終了コード 10） | 無人の経路では承認を求めず、承認資料のパスと番号を報告に載せて終える |
| 棚卸しの報告を返した | 仕上げの終わりの条件を満たし、フェーズレポートを返す |

**コンテキストは 2 つで、関係は顧客 / 供給者である。** 開発ワークフロー（`ndf-workflow`）が顧客で、
「いつ・どのフェーズで棚卸しを通すか」を決める。課題の棚卸し（`ndf-issue-upkeep`）が供給者で、
「1 回に扱う件数」と「書き込む先」を `upkeep.py` の契約として保証する。ワークフローは棚卸しの中身
（区分・見積・順位）を知らず、終了コードと報告だけを受け取る。

## 用語

棚卸し・候補の上限・持ち越しの定義は、設定の用語集（`.ndf/glossary.json` の `document` が指す
[docs/glossary.md](../glossary.md)）にある。

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 棚卸しは仕上げのフェーズの最後の工程にし、新しいフェーズを作らない | フェーズの名前は supervisor の `description` の先頭語と測定の語彙を兼ねる。7 つ目を作ると語彙・測定・起動の表が変わり、supervisor の起動が 1 回増える。中身の分離は規則 14 と上限で得られる |
| 仕上げを作らないモード（`light`）では、取り込みの最後に入れる | 「作らないフェーズの工程は次のフェーズの先頭へ入る」は、後ろにフェーズが無い仕上げには当たらない。前のフェーズの最後へ入れれば、工程表の順序と一致する |
| すべてのモードで必須（工程の分類は 5 モードとも `R`）にする | 候補が 0 件なら `candidates` の 1 回で飛ばすため、費用は候補の収集だけである。条件を付けると、担当の読み方で通るかが変わり、溜まった課題を誰も見ない経路が残る |
| `normal` / `auto` では、仕上げの supervisor が振り返りに続けて同じ起動で通す | フェーズは supervisor 1 つが通す工程のグループである。費用の上限は件数が持つ |
| 候補の上限は `upkeep.py candidates` が自分で決め、呼ぶ側で値を解かない | 上限を必ず有限にする場所を 1 か所にすれば、supervisor・プランのプロンプト・人が打つコマンドのどれからでも同じ上限が効く。呼ぶ側で宣言を読むと、直し忘れた経路が上限なしで残る |
| 既定の上限は 10 件 | 実測は devbase v3.7.0 の 1 回（open 21 件を約 20 分）だけで、変える根拠が無い。1 回を 10 分程度に収める |
| 書き込む先は `apply` が記録のリポジトリと照合して止める | plan の課題は番号だけで指すため、書く先は 1 つのリポジトリに決まる。照合だけで済み、公開の引数を増やさない |
| 無人の経路では、承認の要る反映を報告に載せて終え、承認ゲートにしない | 承認ゲートは 2 つで増やさない。待つと仕上げが止まる。`apply` は承認の無い反映を書かないため、材料を残せば人がいる場面で承認し直せる |
| 持ち越しは報告に載せるまでにする | 次の回で自動で拾うには、候補の集め方か記録の置き場を変える必要がある。受け渡しの置き場は #1640 が持つ |

## 仕様

### 常に成り立つ条件

- **`棚卸し` は、フェーズの表のちょうど 1 つのフェーズ（仕上げ）の「通す工程」の最後にある。** 工程名から
  フェーズを引く対応（`plugins/ndf/scripts/lib/transcript_agents.py` の `STEP_PHASES`）も `"棚卸し": "仕上げ"` である
- **棚卸しは、スプリントを閉じた（`sprint-close.py` が `ok`）後にだけ始まる**
- **`candidates` が区分を求める候補の数は、候補の上限以下である。** 上限は常に 1 以上の整数である
- **`apply` が書き込むのは、記録のリポジトリの課題とマイルストーンだけである**

### `upkeep.py candidates` の候補の上限

上限は `--limit` → `.ndf/backlog.json` の `candidate_limit` → 既定 10 の順に採る。決めるのは
`upkeep_rank_cmd.py` の `candidate_limit`、既定値 `DEFAULT_CANDIDATE_LIMIT` も同じファイルの 1 か所にある。
宣言は `read_backlog_decl` が読む（メインディレクトリの `.ndf/` を先に、無ければ `--root` の `.ndf/`）。

| 入力 | 採る値 | 不正なとき |
| --- | --- | --- |
| `--limit N` | `N` | 1 未満なら `stopped`・終了コード 2。候補を集めない |
| `--limit` を省き、宣言に `candidate_limit` がある | その値 | 1 以上の整数でない（真偽値・文字列を含む）なら `stopped`・終了コード 2 |
| どちらも無い | 10 | — |

- **`--limit` を省いても上限なしにはならない。** `--all` でも上限は効く。全件を 1 回で見るときは `--limit` に
  open の件数以上を渡す
- 結果の `metrics.limit` に採った上限が載る。超えた分は `items` に載せず、`metrics.deferred` に番号、終了コード 20、
  `next` に持ち越しの件数を返す。`report` の結果も `limit` を返す

### `upkeep.py apply` の書き込む先の照合

記録のリポジトリは、同じ状態ディレクトリに保存した `candidates.json` の `metrics.repo` である。書き込み先
（`--repo`、無ければ `gh repo view`）と plan の `repo` を、どちらもこの値と照合する。同じ呼び出しの 2 つを
比べるだけでは、両方を別のリポジトリにすれば通るためである。

| 条件 | 結果 |
| --- | --- |
| `candidates.json` が無い | `stopped`・終了コード 3。1 件も書かない |
| 書き込み先が記録のリポジトリと違う | 同上 |
| plan に `repo` があり、記録のリポジトリと違う | 同上 |
| どれにも当たらない | 反映する |

止まったときは `items[0].reason` に 2 つのリポジトリの名前を載せる。

### スプリントの終わりのプランの `refine` ステップ

`supervise.py new close` が組むプラン（`supervise_lib/sprint.py` の `close_plan`）は、`retro` の後に
`refine` を置く。`retro` の `next` は `refine` で、`retro` の `prompt` は棚卸しを含まない。

| キー | 値 |
| --- | --- |
| `id` | `refine` |
| `type` | `work`（`full: true`） |
| `kind` / `stage` | `棚卸し` |
| `timeout` | 3600 |
| `prompt` | `/ndf:backlog-refinement スプリント <名前>（<課題>）。棚卸しの工程として無人で通す。` に、`--limit` を上げて打ち直さない・終了コード 10 は承認を求めず `presentation_path` と番号を報告して終える・`--repo` を渡さない・親 issue の起票と結び付けを行わず「人の判断待ち」に載せて終える、の 4 点を続ける |
| `next` | `end` |

`refine` へは、`close`（`sprint-close.py`）のステップが 0 で終わったときだけ届く。`timeout` は止まった
worker の歯止めで、費用の上限は件数が持つ。プランの `上限`（実行するステップの数の歯止め）は 12 である。

### 工程表の写し

工程名とモードごとの分類は `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh` の
`WF_STAGE_MATRIX` の 1 か所にある（`棚卸し` は 5 モードとも `R`）。`pace: fast` で記録を求めない工程の
`WF_FAST_DEFERRED_STAGES` にも `棚卸し` が入る。進捗記録の `stage` の値の判定と、課題の本文の「進行」の一覧も
この表を読む。

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| `--limit` を省くと宣言の `candidate_limit`、宣言が無ければ 10 件で切り、`metrics.limit` に採った値が載る | `plugins/ndf/skills/backlog-refinement/tests/test_upkeep_script.py`（`test_candidates_limit_caps_one_run` / `test_candidates_without_limit_cap_at_default` / `test_candidates_limit_comes_from_backlog_decl`） |
| 不正な上限は候補を集めずに終了コード 2 | 同上の `test_candidates_reject_invalid_limit_before_collecting` |
| 候補が 0 件の棚卸しは上限の導入で持ち越しにならない | 同上の `test_candidates_zero_is_ok_under_limit` |
| 書き込み先・plan の `repo`・記録のどれかが食い違えば、1 件も書かずに終了コード 3 | 同上の `test_apply_refuses_*` |
| `new close` のプランで `retro` の次が `refine`、`refine` の `stage` が `棚卸し` で `timeout` を持ち、プロンプトが無人の扱いを渡す | `plugins/ndf/scripts/tests/test_supervise_pace.py`（`test_close_runs_spec_close_and_retro_once_each_in_order`） |
| `STEP_PHASES` がフェーズの表と一致する | `plugins/ndf/scripts/tests/test_transcript_agents.py`（`test_the_step_names_match_the_phase_table`） |
| `stage 棚卸し` が記録され、5 モードで必須として扱われる | `plugins/ndf/scripts/tests/test_progress_record_stages.py`（`test_the_refinement_stage_is_recorded_and_required_in_every_mode`） |
| 最終工程を持つ 3 つの Skill（`retrospective` / `release-verification` / `release`）が棚卸しを呼ばない。順序の制約と規則 14 がそれぞれ 1 か所にある | 文書を `grep -n` で読んで確かめる（文言を照合するテストは書かない） |

## 運用

- **既定の上限 10 件の妥当さは、リリース後テストで件数・秒・トークンを取って見直す。**
- **無人の経路の承認資料は worker の報告に載るだけで、キューの `gate` にはならない。** conductor が最後の
  報告で人へ渡す
- **ボードの `進行` を単一選択で作ったリポジトリには `棚卸し` の選択肢が無い。** `projects-sync.sh` は
  ボードへの書き込みを飛ばす（課題の本文の「進行」と通過記録には残る）。選択肢を足すのは利用者である
  （`progress-tracking` の `references/board.md`）
- **人が工程の外で打つ `--all` にも上限が掛かる。** 全件を見るには `--limit` を上げる

## 関連リンク

- [#842](https://github.com/devbasex/ai-plugins/issues/842) — 要求と決定（2026-09-30 利用者の決定: 棚卸しを工程表の行にする）
- [#1640](https://github.com/devbasex/ai-plugins/issues/1640) — 持ち越しを次のスプリントへ受け渡す置き場
- [課題を根本原因の場所で直す区分（ルートコーズ）](ndf-issue-upkeep-root-cause.md) — 棚卸しの区分の判断
- [後片付けを止めずに通し、スプリント課題を最終工程で閉じる](ndf-cleanup-and-bundle-closing.md) — 「スプリントを閉じる」の条件と結果
