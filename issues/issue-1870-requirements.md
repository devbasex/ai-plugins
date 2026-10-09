# #1870: 配布の記録とリリース後テストの記録を、sprint-close が読む形で書く側が書く

正は課題の本文（#1870）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を直すか
>
> スプリントの課題を閉じる `sprint-close.py` は、本番の「配布の記録」とリリース後テストの記録を読んで閉じてよいかを決める。その記録を書く側（release・release-verification・本番のプラン）が、読む側の求める形と時点で書いていない。そのため現象は読む側（`sprint-close.py`）で現れるが、原因は書く側にある。
>
> ## 修正レイヤー
>
> **配布の記録とリリース後テストの記録を書く側の契約。**
>
> - `plugins/ndf/scripts/release_lib/record.py` の記録の形（`版:` の値）と、release-verification の書式（`対象の版:`）
> - 配布の形ごとの記録のステップ（package-plugin の `record` はある。`delivery_templates.py` の `plan_promote` の後には無い）
> - 本番のプラン（`supervise_lib/release_templates.py`）に `sprint-close.py --with-verification` のステップが無い
>
> 読む側（`plugins/ndf/scripts/lib/dist_record.py` の `parse_record`・`_pick_verify_block`、`sprint-close.py` の `_closing_blocker`）を緩めるのは補助にとどめる。
>
> ## 修正方針
>
> - **統合**: 記録の形を `release_lib/record.py` の 1 か所で決め、release-verification はその値を写す（`centralize_configuration`）
> - **移動**: 課題を閉じる工程を、conductor の手から本番のプランのステップへ移す（`move_responsibility`）
>
> ## 子 issue
>
> | 課題 | 現象レイヤー | 観測 |
> | --- | --- | --- |
> | #837 | `sprint-close.py` が配布の記録を読めず `段階: 検証` で止まる。プランの経路では本番まで出たスプリントも「配布なし」で閉じる | devbase#212（v3.7.0） |
> | #1789 | 版数を上げない配布で `版:` と `対象の版:` の形が違い、課題がすべて kept_open になる | devbase PR #432（ndf 10.17.63） |
> | #1683 | 受け入れ条件とリリース後テストが未チェックのまま課題が閉じる | m1340・m1649・m815・m1847 |
>
> 子 issue は閉じない。原因を直した後も、promote の記録のステップ（#837）・版数を上げない配布の形（#1789）・本文のチェックボックスの更新（#1683）の個別の作業が残る。
>
> ## 完了条件
>
> - [ ] 配布の記録とリリース後テストの記録の形が `release_lib/record.py` の 1 か所で決まり、配布の形（package-plugin・promote）と版数を上げるか否かによらず、`sprint-close.py` が同じ規則で読める
> - [ ] 本番のプランが `sprint-close.py --with-verification` のステップを持ち、conductor がプランの外で課題を閉じない
> - [ ] 子 3 件の再現手順が、直した後の版で通る

## 目的

- リリース記録とリリース後テストの記録を書く側（スクリプトと Skill の手順）が、`sprint-close.py` が読む形と時点で書く。配布の形（package-plugin・promote）と版数を上げるか否かによらず、本番まで出たスプリントの課題が、プランのステップの中の `sprint-close.py --with-verification` で閉じる
- conductor がプランの外で課題を閉じる手当て（m1340・m1649・m815・m1847）を無くす（Value 4）。読む側を緩めて現象ごとに受け止めず、書く側の契約で直す（Value 6）

## 前提

- 前提 1: 依頼の「`release_lib/record.py` の 1 か所」は、記録の形を持つ `plugins/ndf/scripts/lib/dist_record.py`（`format_record` と、見出し・行の名前の定数）と読む。`release_lib/record.py` はその形で組んで投稿する書き手である（両ファイルの冒頭の docstring が、形の持ち主を `lib/dist_record.py` としている）。どちらへ寄せるかの最終の置き場は設計が決める
- 前提 2: 依頼の「本番のプランが `sprint-close.py --with-verification` のステップを持つ」は、「本番の配布より後に流れるスプリントのキューのプランが、そのステップを持つ」と読む。いまスクリプトで組むプランのどれも `## リリース後テスト` を書かないため（`supervise_lib/` に `release-verification` を呼ぶステップが無い。2026-10-09 に grep で確認）、本番の配布の直後に閉じると全課題が `kept_open` になる。ステップを本番のプランに置くか、まとめのプラン（`supervise_lib/sprint.py` の `close_plan`、既に `close` を持つ）の入力を直すかは設計が決める
- 前提 3: 版数を上げない配布で `版:` の右と `対象の版:` に書く値（版数か、配布したコミット `<ブランチ> <短い SHA>` か）は、両方が同じ規則で 1 つの値を書くことだけを要求とし、どちらの値を採るかは設計が決める
- 前提 4: 配布の形のうち、スクリプトが配布を実施する形は package-plugin（`release_templates.py`）と promote（`delivery_templates.py` の `plan_promote`）の 2 つとする。手動反映の本番系（`plan_gate_2`。本番のデプロイはプランの後に人か担い手が行う）とスクリプトで組まない配布は、`release` の Skill の手順（LLM が書く）で同じ形を書く
- 前提 5: 子 issue（#837・#1789・#1683）は閉じない（依頼の「子 issue」の節）。この課題の受け入れ条件は、子の再現手順が直した後の版で通ることまでを持つ

## 対象範囲

含む:
- リリース記録とリリース後テストの記録の形（見出し・行の名前・`版:` と `対象の版:` の値の規則）を 1 か所で持つこと。版数を上げない配布の値の規則を含む
- promote の形で、配布（昇格の Pull Request のマージ）の後にリリース記録を書くステップ
- 雛形で組まない経路（`route_waves`）のまとめが、本番まで出たスプリントではリリース記録を置いた Pull Request を `--record-pr` に渡すこと。`--record-pr 0` を本当に配布しなかったときだけに戻すこと
- `release-verification` が `対象の版:` の値をリリース記録から写す手段と手順
- `release` の SKILL.md の手順 3 と「リリース記録」の節に、配布の形によらず「配布を実施した後に、実施後の値でリリース記録を置く」段を持たせること（#837 の案 A）
- 本番の配布より後に流れるプランのステップが `sprint-close.py --with-verification` を打ち、リリース後テストの記録がそろった後に課題を閉じること

含まない:
- 読む側（`lib/dist_record.py` の `parse_record`・`_pick_verify_block`、`sprint-close.py` の `_closing_blocker`）の一致規則の緩和。補助として要ると設計が判断したときだけ、別の受け入れ条件として足す
- 課題を閉じるとき、本文の受け入れ条件と工程表のチェックボックスを更新すること（#1683 の残る論点 2）
- コミットの件名の closing keywords（GitHub が課題を自動で閉じるキーワード。`Closes #N` など）で GitHub が先に閉じる経路を塞ぐこと（#1683 の残る論点 3）
- リリース後テストそのもの（受け入れ条件の確かめ）をスクリプトで行うこと
- 既に閉じた過去のスプリントの記録を書き直すこと

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 配布の承認を得た（承認ゲート 2） | 人の承認か、MVV 判定 | 承認されなければ配布しない。記録も書かない | 検査が通っている |
| E2 | 本番へ配布した（タグを出した / 昇格の Pull Request をマージした / 手動で反映した） | E1 | 配布が落ちたらプランが止まり、リリース記録は書かない | E1 |
| E3 | 実施後の値でリリース記録を置いた | E2 の直後のプランのステップ（スクリプトで配布する形）か、`release` の手順（それ以外の形） | 書き込みが落ちたら、配布へ戻らずに記録のやり直しか停止を選ぶ（package-plugin の `judge-record` と同じ扱い） | E2。承認の前に置いた記録があっても、E3 の記録が最後のブロックになる |
| E4 | 導入の確認をした（`verify-install` / `verify-facts`） | E2 | 落ちたらプランが止まる | E2 |
| E5 | リリース後テストの記録を置いた（`対象の版:` は E3 の記録の値を写した） | `release-verification` | 記録が無いか値が一致しなければ、E6 は全課題を `kept_open` にする | E3 |
| E6 | スプリントの課題を閉じた（`sprint-close.py --with-verification`） | プランのステップ | 条件を満たさない課題は `kept_open`、閉じられなければ `failed` | E3・E5 |

E5 の引き金を持つプランのステップは今は無い（前提 2）。E5 をプランの中で誰が起こすかは未決に置く。

## 用語

| 用語 | 意味 |
| --- | --- |
| リリース後テストの記録 | `release-verification` が Pull Request へ残す、見出し `## リリース後テスト` から `合否:` までのブロック。`対象の版:` の行と課題ごとの表を持つ |
| 版数を上げない配布 | 版数を変えず、既定ブランチへのマージかコミットの反映で本番へ届ける配布（devbasex/devbase の m7e） |

既存の語: リリース記録（`## 配布の記録` のブロック）・昇格のプラン・昇格の Pull Request は用語集のとおりに使う。

## 受け入れ条件

- [ ] 1. 見出し `## 配布の記録`・`## リリース後テスト` と、行の名前 `段階: `・`版: `・`スプリント: `・`対象の版: ` の文字列を定義するのは 1 つのモジュールだけで、記録を組む・読むほかのスクリプト（`release_lib/`・`supervise_lib/`・`sprint-close.py`・`release-verification-steps.py`）はそのモジュールの定数か関数を使う
- [ ] 2. 版数を上げない配布（前の版と新しい版が同じ）で、書く側の規則で組んだリリース記録と、`release-verification` の手順で写したリリース後テストの記録を同じ PR に並べると、`parse_record` の `verify_block` がそのリリース後テストのブロックになる（#1789 の再現: devbase PR #432 の 2 つの記録と同じ配布を、書く側の規則で書き直した組）
- [ ] 3. 版数を上げる配布（package-plugin の `release-steps.py record` が書く `版: 10.17.67 → 10.17.68` の形と、`対象の版: 10.17.68（…）`）で、`parse_record` の結果が直す前と変わらない
- [ ] 4. `release-verification` の手順に、`対象の版:` の値をリリース記録から得るコマンドがあり、そのコマンドの出力をそのまま `対象の版:` へ書いたブロックが 2 の規則で選ばれる
- [ ] 5. 昇格のプラン（`plan_promote` の normal と fast / auto の両方）が、昇格の Pull Request のマージの後にリリース記録を書くステップを持つ。そのステップが書いた記録を `sprint-close.py` が `段階: 本番` で始まる記録として読む
- [ ] 6. 5 のステップが落ちたとき、プランは配布（マージ）をやり直さず、記録のやり直しか停止へ進む
- [ ] 7. 雛形で組まない経路のまとめの `close` ステップは、本番まで出たスプリントではリリース記録を置いた Pull Request の番号を `--record-pr` に渡す。`--record-pr 0` を渡すのは、最終の検査で変更が無く本番を飛ばしたときだけである（#837 のプランの経路での現れ方の再現）
- [ ] 8. 承認の前に `段階: 検証（… 承認待ちのため未実施）` の記録を本文に置いた PR に、配布の後に 5 か package-plugin の `record` が記録を書くと、`sprint-close.py` は `段階: 本番` として読む（#837 の再現: devbase PR #212 の本文の形）
- [ ] 9. `release` の SKILL.md の手順が、配布の形によらず「配布を実施した後に、実施後の値でリリース記録を置く」段を持ち、スクリプトが書く形（package-plugin・promote）ではそのステップを指す
- [ ] 10. package-plugin の経路と promote の経路の両方で、スプリントのキューの中に、本番の配布とリリース後テストの記録より後に `sprint-close.py --with-verification` を打つステップがある（#1683 の残る論点 1 の再現: 生成したプランの JSON に、そのステップがある）
- [ ] 11. 10 のステップは、リリース後テストの記録が無いときに課題を閉じない（`kept_open` で残す。今の閉じる条件 2 を変えない）
- [ ] 12. 子 3 件（#837・#1789・#1683）の再現手順が、上の 2・5・7・8・10 のテストとして直した後の版で通る
- [ ] 13. 退行しない: `plugins/ndf/scripts/tests` の全体テストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 記録の形を変えるとき、直すモジュールは 1 つである（受け入れ条件 1） |
| 移行性 | 直す前の版で書かれた記録（版数を上げる配布の `版:`・`対象の版:`、改名の前の `スプリント:` の見出し）を、直した後の `parse_record` も同じ結果で読む |
| システム環境 | 配布の形・版数の規則・既定ブランチ・本番チャネルは、`.ndf/` の宣言か引数から受け、ai-plugins の値（`main` / `develop`・`ndf--v<版数>` のタグ）を既定に埋め込まない（Value 5） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`plan_promote` のプランのステップが増え、`release-verification` の書式の `対象の版:` の値の規則が決まる。`sprint-close.py` の引数と結果 JSON の形は変えない。互換の経路: 直す前の形の記録も読める（移行性） |
| データ | 無し（PR のコメントに書く記録の形だけ） |
| 既存の振る舞い | 雛形で組まない経路で本番まで出たスプリントは、「配布なし」として閉じず、リリース記録とリリース後テストの記録を見て閉じる。リリース後テストの記録が無ければ `kept_open` になる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4` |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`・`claude plugin validate .`（終了コードで見る） |
| 手動確認 | リリース後テストで、次に promote の形か版数を上げない配布で本番まで出たスプリント（devbasex/devbase）で、プランの中の `sprint-close.py --with-verification` が課題を `closed` にし、conductor が手で閉じていないことを、キューの記録と課題の閉じた主体で確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。スクリプトは `plugins/ndf/scripts/`、手順は `plugins/ndf/skills/` |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。Skill の frontmatter は `plugins/ndf/skills/AUTHORING.md` と `scripts/check-skill-frontmatter.py` |
| テスト戦略 | 記録の組み立てと読み取りは `lib/dist_record.py` の単体テスト、プランの形は雛形の生成結果（JSON）のテスト。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、変更範囲内の記録の定数の集約 |
| 確認してから行う | プランのステップの追加と、`release` / `release-verification` / `progress-tracking` の手順の書き換え（承認ゲート 1・2 で人が見る） |
| 行わない | 読む側の一致規則の緩和（含まないの 1 行目）、子 issue を閉じること、過去の PR の記録の書き直し、コミットと PR 本文への closing keywords |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 版数を上げない配布で `版:` の右と `対象の版:` に書く値（版数か、配布したコミットか）（前提 3） | 設計（`design`） | 承認ゲート 1 |
| 課題を閉じるステップを本番のプランに置くか、まとめのプランの入力を直すか（前提 2） | 設計 | 承認ゲート 1 |
| プランの中でリリース後テストの記録（E5）を誰が置くか。プランのステップとして `release-verification` を起こすか、プランを止めて人か conductor が置くのを待つか | 設計 | 承認ゲート 1 |
| 形の定数の置き場を `lib/dist_record.py` のままにするか `release_lib/record.py` へ移すか（前提 1） | 設計 | 承認ゲート 1 |
