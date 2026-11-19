## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 4 点は同じ原因（11.0.0 の作り直しで、`rank` の作りを手順書に合わせ直していない）による | 4 点とも 10 系の手順書の記述がそのまま残っている | 前提 1 |
| S2 | 直す側（コードか手順書か）は設計で決める | 起票者の判断 | 前提 2（4 点ともコードを手順書に合わせる。理由は設計文書の決定の記録） |

## 事象

`backlog-refinement` の手順（`plugins/ndf/skills/backlog-refinement/SKILL.md` の手順 2C・手順 3 と `references/ranking.md` の「見積の入力」）が書く入力・引数・返りが、11 系の `ndf issues rank` / `apply`（`plugins/ndf/src/ndf/domains/backlog/`）と 4 か所で食い違う。10 系（`ndf--v10.27.77` の `plugins/ndf/skills/backlog-refinement/scripts/upkeep*.py`）は 4 か所とも手順書のとおりに動いていた。

| # | 手順書 | 11.0.8 のコード |
| --- | --- | --- |
| 1 | 見積の入力は `"ubv": {"step": 13, "why": "...", "impact": "全体"}` の形 | `model.parse_estimate` は `ubv`・`tc`・`rr_oe`・`size` を整数で読む。手順書の形を渡すと 7 件すべてが「UBV が尺度 [1, 2, 3, 5, 8, 13, 20] に無い: {'step': 5, ...}」で外れる。設定のスキーマに `harm_guide` が無い |
| 2 | 差分の棚卸しでは `rank` に `--since-ref` を渡し、起点より前に作られた見積の無い課題は `metrics.left_for_review` へ返す | `rank` に `--since-ref` が無い（`ndf issues rank --help`）。返りに `left_for_review` が無く、見積の無い課題 308 件を `excluded` にしたまま終了コード 0 で返す。`candidates` の差分の棚卸しの `unscored` も起点の時刻で絞らず、`metrics.since` を返さない |
| 3 | 見積は `--scores` → 記録 → マイルストーンの説明の `### 順位` の表の順に採る | 3 つ目の出どころは手元の `rank.json` で、説明を読まない。説明に `### 順位` の表（15 行）を持つマイルストーン`40 入れ替えの後の実地の検証と、契約・テスト・依存・確定仕様の土台`の #3572・#3692 も「見積が無い（採点する）」で外れる |
| 4 | plan に `repo` を書くと、`apply` が記録のリポジトリと照らす（SKILL.md の手順 3） | `ndf issues apply` が「plan の知らないキー（1 件も書いていない）: repo」で終了コード 2 を返す。書き込み先を記録のリポジトリと突き合わせる検査そのものも無い |

手順書には、11 系が持たない振る舞いの記述がほかに 4 つある。

- 見積の入力の `milestones`（着手の順）。11 系は引数 `--milestones` だけを読む
- `### 並列の組（見込み）` の依存を `rank` が読む（`ranking.md`）
- 前回の `### 順位` の表からの変化を返す（SKILL.md の手順 2C）
- 返りの表の「20（`excluded` あり）」。11 系は差分の棚卸しで 0、見直しで 6（`incomplete`）を返す

## 期待する結果

- 手順書の見積の入力の形を `rank` がそのまま読む
- 差分の棚卸しで、起点より前の課題を `excluded` にせず、見直しへ回す
- マイルストーンの説明の `### 順位` の表の見積を採る
- plan の `repo` を `apply` が受け、記録のリポジトリと照らす

## 再現手順

1. `ndf issues candidates --since-ref ndf--v11.0.7 --depth diff --add 3842 --limit 20` を打つ（2026-11-19、終了コード 0）
2. `ranking.md` の例の形で `#3842` の見積を書き、`ndf issues rank --scores <JSON> --depth diff` を打つと、#3842 が「尺度に無い」で外れる
3. 同じ見積を `"ubv": 5` の整数の形にして打つと、7 件で算出し、外した課題を 308 件と返す。`metrics` のキーは `backward, depth, digest, dropped, excluded, forward, order, rejected_stale, repo, sized` で、`left_for_review` が無い

## 環境

NDF 11.0.8（導入先 `~/.claude/plugins/cache/ai-plugins/ndf/11.0.8`、main 2e43af7bc と同じ中身）・Claude Code・Linux

## 前提

- 前提 1: 4 点は、11.0.0 の作り直しで 10 系の振る舞いを `rank` / `apply` へ移しきらなかったことによる。作り直しの設計（#1142）に、4 点を意図して外した記述は無い
- 前提 2: 4 点とも手順書を SSOT とし、コードを手順書（10 系の振る舞い）に合わせる。11 系に無い 4 つの記述（入力の `milestones`・並列の組の依存・前回からの変化・返りの 20）は手順書から消すか、11 系の返りに書き直す
- 前提 3: 説明の `### 順位` の表から読み戻すのは、11 系の `apply` が書く表の形だけである。10 系の形の表（12 列）は今開いているマイルストーンに無く、読まない
- 前提 4: 見積の入力は 1 つの形（手順書の形）だけを読む。整数の形は読まない（普及の前で、後方互換は要らない）

## 対象範囲

含む:
- `rank --scores` が手順書の見積の入力の形を読み、根拠（`why`）の欠けと UBV の実害の目安の帯の外れを外す
- 設定 `.ndf/backlog.json` の `harm_guide` を設定のスキーマが受ける
- `rank --since-ref` と `metrics.left_for_review`、`candidates` の差分の棚卸しの `unscored` の絞り込みと `metrics.since`
- 見積の 3 つ目の出どころとして、マイルストーンの説明の `### 順位` の表を読む
- plan の `repo` と、`apply` の書き込み先と記録のリポジトリの突き合わせ
- 手順書を、上の振る舞いと 11 系の返り（終了コード）に合わせて直す

含まない:
- 10 系の形の `### 順位` の表の読み戻し（前提 3）
- `apply` が表を書き換えるときの 10 系の終わりの印 `<!-- ndf:section-end -->` の扱い（#3861）
- `### 並列の組（見込み）` の依存の読み戻しと、前回の表からの変化の算出（手順書から消す）
- 見積の段階の付け方（手順 2A・2B）と、順位の式の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 候補を集めた | `ndf issues candidates` | GitHub を読めなければ止まる | — |
| E2 | 見積の入力を読んだ | `ndf issues rank --scores` | 形の違う課題は `excluded` へ入る | E1 |
| E3 | 課題ごとに見積の出どころを決めた | E2 | どこにも無く起点より後の課題は `excluded`、起点以前は `left_for_review` へ入る | E2 |
| E4 | 順位を算出して記録へ残した | E3 | 依存の循環で止まる | E3 |
| E5 | plan を読んで書き込み先を照らした | `ndf issues apply` | 記録が無い・リポジトリが違えば 1 件も書かずに止まる | E4 |
| E6 | マイルストーンの説明の順位の表を書き換えた | E5 | — | E5 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 見積の入力 | `ndf issues rank --scores` に渡す、課題ごとの見積の段階と根拠の JSON |
| 見積の根拠 | 見積の入力の列ごとの `why`。段階を付けた理由の 1 行 |
| 実害の区分 | 見積の入力の `ubv.impact`。UBV の帯（実害の目安）を引く区分 |

## 受け入れ条件

- [ ] 1. `rank --scores` に `ranking.md` の例の形（4 列が `{"step", "why"}`、`ubv` が `impact` も持つ）の見積を渡すと、その課題が順位に入る
- [ ] 2. 4 列のどれかの `why` が空か無い課題、整数の形（`"ubv": 5`）の課題は、`excluded` に理由（根拠が空・形が違う）を添えて入る
- [ ] 3. `ubv.impact` を持つ課題の UBV が実害の目安の帯（`.ndf/backlog.json` の `harm_guide`、既定は全体 13〜20・機能 5〜8・表示 2〜3・なし 1）の外なら `excluded` に入る。`mitigation` が `防いでいる` なら 1 つ下の帯の段階も受ける
- [ ] 4. `harm_guide` を書いた `.ndf/backlog.json` を、設定のスキーマ（`backlog.schema.json`）と `rank` が受ける
- [ ] 5. 見積の根拠と実害の区分は記録（`estimates.json`）に残り、次の回に記録から採った見積も根拠を持つ
- [ ] 6. `rank --depth diff --since-ref <ref>` で、見積がどこにも無く `<ref>` のコミットの時刻以前に作られた課題は、`excluded` にも順位にも入らず、`metrics.left_for_review` に番号で返る。結果のスキーマ（`issues.result.schema.json`）がこの項目を受ける
- [ ] 7. `candidates --depth diff --since-ref <ref>` の `unscored` は `<ref>` のコミットの時刻より後に作られた課題だけで、`metrics.since` にその時刻を返す
- [ ] 8. `rank` は、`excluded` が 1 件以上あれば棚卸しの深さによらず `status: stopped`・`cause.kind: incomplete`（終了コード 6）で返し、0 件なら `ok` で返す
- [ ] 9. `--scores` にも記録にも見積が無い課題は、そのマイルストーンの説明の `### 順位` の表（11 系の `apply` が書く形）の行の段階を採る。記録の無い手元で、表に行を持つ課題が `excluded` に入らない
- [ ] 10. 一部の課題だけに見積を付けて `rank` を打ち、その `digest` を plan に入れて `apply` を打っても、表に行を持っていた課題の行が消えない
- [ ] 11. plan の `repo` を `apply` が受ける。書き込み先（`--repo`）か plan の `repo` が記録のリポジトリ（`candidates.json` の `metrics.repo`）と違うとき・記録が無いときは、1 件も書かずに `status: stopped`・`cause.kind: precondition`（終了コード 3）で止まる
- [ ] 12. 手順書（`SKILL.md`・`references/ranking.md`・`references/depths.md`）が、見積の入力の `milestones`・`### 並列の組（見込み）` の依存の読み戻し・前回の表からの変化・返りの 20 を書かず、8 の返りを書く
- [ ] 13. 退行しない: 既存の `plugins/ndf/tests/domains/backlog/` のテストが CI で通る（整数の形の入力を使うテストは、手順書の形へ直す）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`rank --scores` の入力の形（整数の形を読まなくなる。`互換なし`）、`rank` の引数 `--since-ref`・返りの `metrics.left_for_review`・終了コード、`candidates` の返りの `metrics.since`、plan の `repo`、設定の `harm_guide` |
| データ | 記録 `estimates.json` の見積の行が根拠と区分を持つ。前の形の行（整数）は読み戻すときに根拠の無い見積として扱う |
| 既存の振る舞い | 差分の棚卸しで外した課題があると `rank` が 0 でなく 6 を返す |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | PR の CI（`.ndf/project.json` の `test.ci.only`）。`ndf issues` のコマンドを偽の GitHub の読み口で打つテスト |
| 手動確認 | リリース後テストで、次の棚卸しの手順 2C を手順書のとおりに打ち、見積が外れずに順位が出ることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `plugins/ndf/src/ndf/domains/backlog/`。判定は `*_decide.py` の純粋な関数、GitHub と記録の読み書きは `issues_gh.py`・`state.py`・`tables.py` |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」 |
| テスト戦略 | 判定の関数の単体テストと、`ndf issues` のコマンドのテスト。`.md` の文言を照らすテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手順書の例の形をそのままテストの入力に使う |
| 確認してから行う | 順位の式・尺度の既定値の変更（設計承認ゲート） |
| 行わない | GitHub のマイルストーンの説明を試しに書き換えること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 無し | — | — |

## リクエスト（原文）

> ## 事象
>
> `backlog-refinement` の手順（`plugins/ndf/skills/backlog-refinement/SKILL.md` の手順 2C と `references/ranking.md` の「見積の入力」）が書く入力・引数・返りが、11 系の `ndf issues rank`（`plugins/ndf/src/ndf/domains/backlog/`）と 4 か所で食い違う。
>
> | # | 手順書 | 11.0.8 のコード |
> | --- | --- | --- |
> | 1 | 見積の入力は `"ubv": {"step": 13, "why": "...", "impact": "全体"}` の形 | `model.parse_estimate` は `ubv`・`tc`・`rr_oe`・`size` を整数で読む。手順書の形を渡すと 7 件すべてが「UBV が尺度 [1, 2, 3, 5, 8, 13, 20] に無い: {'step': 5, ...}」で外れる |
> | 2 | 差分の棚卸しでは `rank` に `--since-ref` を渡し、起点より前に作られた見積の無い課題は `metrics.left_for_review` へ返す | `rank` に `--since-ref` が無い（`ndf issues rank --help`）。返りに `left_for_review` が無く、見積の無い課題 308 件を `excluded` にしたまま終了コード 0 で返す |
> | 3 | 見積は `--scores` → 記録 → マイルストーンの説明の `### 順位` の表の順に採る | 説明に `### 順位` の表（15 行）を持つマイルストーン「40 入れ替えの後の実地の検証と、契約・テスト・依存・確定仕様の土台」の #3572・#3692 も「見積が無い（採点する）」で外れる |
> | 4 | plan に `repo` を書くと、`apply` が記録のリポジトリと照らす（SKILL.md の手順 3） | `ndf issues apply` が「plan の知らないキー（1 件も書いていない）: repo」で終了コード 2 を返す |
>
> ## 期待する結果
>
> - 手順書の見積の入力の形を `rank` がそのまま読む。手順書をコードの形に直すのでもよい。どちらかが SSOT になる
> - 差分の棚卸しで、起点より前の課題を `excluded` にせず、見直しへ回す（手順書のとおり）か、手順書からその記述を消す
> - マイルストーンの説明の `### 順位` の表の見積を採る。採らないなら手順書から消す
> - plan の `repo` を `apply` が受けるか、手順書から消す
>
> ## 再現手順
>
> 1. `ndf issues candidates --since-ref ndf--v11.0.7 --depth diff --add 3842 --limit 20` を打つ（2026-11-19、終了コード 0）
> 2. `ranking.md` の例の形で `#3842` の見積を書き、`ndf issues rank --scores <JSON> --depth diff` を打つと、#3842 が「尺度に無い」で外れる
> 3. 同じ見積を `"ubv": 5` の整数の形にして打つと、7 件で算出し、外した課題を 308 件と返す。`metrics` のキーは `backward, depth, digest, dropped, excluded, forward, order, rejected_stale, repo, sized` で、`left_for_review` が無い
>
> ## 環境
>
> NDF 11.0.8（導入先 `~/.claude/plugins/cache/ai-plugins/ndf/11.0.8`）・Claude Code・Linux
>
> ## 影響
>
> - 手順書どおりに打つと、見積がすべて外れて順位が出ない
> - 一部の課題だけに見積を付けて `rank` の `digest` を plan に入れると、`apply` が各マイルストーンの説明の `### 順位` の表をその数件だけの表で書き換える。既存の順位の表が消える。この回の棚卸しでは、plan に `rank` を入れずに避けた
>
> ## 起票者の推定
>
> - 4 点は同じ原因（11.0.0 の作り直しで、`rank` の作りを手順書に合わせ直していない）によるとみる（推した出所: 4 点とも 10 系の手順書の記述がそのまま残っている）
> - 直す側（コードか手順書か）は設計で決める（推した出所: 起票者の判断）
>
> ## 由来
>
> issue #3812 の仕上げのフェーズの棚卸し（2026-11-19）で、手順 2C を打って見つけた。#3812 は中継のプラグインの更新で、棚卸しの道具は範囲外。根拠: Value 3「同じ手順は毎回同じ結果になる」（MVV バージョン 9）。

（書き手: 起票者、日付: 2026-11-19。書き手が利用者でない文は、利用者の指示の原文として扱わない）

