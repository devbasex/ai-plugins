# NDF のスクリプト: ラベル `rebuild-1142` の Issue のうち既にある子 Issue の範囲に入らないものを、14 本の子 Issue で閉じる（#1142）

## 目的

- **今どうなっているか**: #1142 の受け入れ条件に「ラベル `rebuild-1142` の Issue（開いているもの 283 件）が #1142 の改善ですべて閉じる」が足された（2026-11-13 利用者）。283 件のうち 110 件は、既にある子 Issue（A・DC・SF・K・W・RF）の変更の範囲に入らない
- **誰に効くか**: NDF の利用者（キューの再開・並列の計画・中継・cross-review・cross-refactoring・棚卸し・記録・テストの不具合が減る）と、#1142 を流す conductor（ラベルの Issue の行き先が 1 つに決まる）
- **変えると何が成り立つか**: ラベルの Issue のうち閉じられない 6 件を除く 277 件が、どれか 1 本の子 Issue の PR で閉じる。行き先の表は [issue-1142-design-labels.md](issue-1142-design-labels.md) にある

## 適用範囲

- **働く範囲**: `plugins/ndf/` のスクリプト・Skill の手順書・契約と、このリポジトリの検査とテスト。利用先のリポジトリでの振る舞いは、それぞれの Issue の本文の範囲だけ変わる
- **プロジェクトごとに違うもの**: 無し（各 Issue の直しが宣言を読む箇所は、既にある宣言の置き場を使う）
- **当たるモード**: `standard`（#1142 の設計の一部）

## あるべき姿の根拠

| 根拠 | 種類 | 何を示すか |
| --- | --- | --- |
| 「#1142 の受け入れ条件は、このタグの Issue が #1142 の改善によってすべて閉じること」（2026-11-13 利用者） | 利用者の指示の原文 | ラベルの Issue をすべて子 Issue の範囲へ入れる |
| `.ndf/handoff/rebuild-1142-classification.json`（開いている 375 件の分類） | 実測 | 作り直しで閉じられるとした 227 件と、子 Issue 56 本にラベルを付けた |
| 割り当ての下調べ（`.ndf/parallel/p6/work/rebuild-assign/all.json`）と、新しい子の候補 116 件の本文とコードの読み直し（2026-11-13） | 実測 | 各 Issue が名指すファイルと関数、実装が develop に既にあるもの |
| #1142 の前提（後方互換を意識しない・テストは 1 から作り直してよい・AI が陥りやすい作りの撲滅・定数からデータへ） | 利用者の指示の原文 | 直し方は握りつぶさずに止まった理由の種類で返し、行き先をスクリプトが選ぶ形にそろえる |

要求と受け入れ条件は #1142 の本文にある（コピーは [issue-1142-requirements.md](issue-1142-requirements.md) ）。全体のドメインモデルとコンテキストは [issue-1142-design.md](issue-1142-design.md) にあり、この文書では描き直さない。ファイルと関数の粒度は [issue-1142-design-fixes-design-detail.md](issue-1142-design-fixes-design-detail.md) にある。

## 子 Issue の分け方

割り当ての下調べの 10 本の案（`new-groups.json` の N1〜N10）を、触るファイルと主題で組み直した。1 本の子 Issue は、触るファイル 60 本以下・解消する Issue 15 件以下に収める。

| 子 Issue | 主題 | 解消する Issue | 触るファイル | 先に要るもの | 下調べの案からの変更 |
| --- | --- | ---: | ---: | --- | --- |
| `N1` | キューの再開と参照の解決 | 13 | 26 | `SF1a`・`SF2`・`RF4`・`RF5a`・`RF5b`・`RF7`・`RF1b`・`SF1b`・`SF11` | 案 N1 から #3086・#3176・#3114・#3047 を RF4・RF5a へ（#2963 の設計で割り当て済み）、#2349 を SF2・#2639 を SF13 へ移した |
| `N2a` | worker の受け渡しと指示文 | 7 | 19 | `SF1a`・`SF1b`・`SF2`・`RF5a` | 案 N2 を 2 本に分けた（worker の受け渡し）。#2340 を SF1b へ移し、#2341 は閉じられないとした |
| `N2b` | 設計の結果・受け入れ条件・重なりの判定・マージの後の作業場所 | 13 | 27 | `SF1a`・`SF1b`・`SF2`・`RF5a`・`A1b` | 案 N2 を 2 本に分けた（設計の結果・受け入れ条件・重なり・マージの後） |
| `N3` | 並列の計画の状態とレーンの生存 | 10 | 18 | `SF1a`・`RF6a`・`RF6b`・`SF1b`・`SF9` | 案 N3 から #2585 を RF6a、#2999・#3021・#3064・#3145 を RF6b へ移した |
| `N4` | 中継・hook の転送・依存の環境 | 11 | 25 | `SF1a`・`DC1`・`DC5`・`SF12`・`SF1b`・`SF6`・`A2` | 案 N4 のまま |
| `N5` | cross-refactoring の振る舞い | 8 | 33 | `SF1a`・`SF1b`・`DC5` | 案 N5 のまま |
| `N6` | cross-review の同期と worktree | 5 | 16 | `SF1a`・`SF1b`・`RF1b`・`K1`・`A2`・`SF13`・`DC4` | 案 N6 のまま |
| `N7` | 棚卸し（backlog-refinement） | 6 | 16 | `SF1a`・`SF1b`・`K1`・`SF2`・`DC5`・`A2` | 案 N7 のまま |
| `N8a` | 工程の終わりの記録・引継ぎ文書・用語集 | 6 | 18 | `SF1a`・`SF1b`・`SF13`・`A2`・`DC6`・`DC3`・`A1a`・`DC5` | 案 N8 を 2 本に分けた（記録・引継ぎ・用語集）。#2316 は閉じられないとした |
| `N8b` | 使用量の帳簿と MVV の記録 | 7 | 8 | `SF1a`・`SF1b`・`SF13`・`A2`・`DC6`・`DC3`・`A1a`・`DC5` | 案 N8 を 2 本に分けた（使用量・MVV） |
| `N9` | テストの揺れ・残骸・隔離・後始末 | 9 | 28 | `RF2`・`DC5`・`SF6`・`SF11`・`RF7`・`SF12` | 案 N9 から #2711 を N12 へ分けた |
| `N10` | 監視・外部 AI・マージの hook | 5 | 28 | `SF1a`・`SF1b`・`A2`・`DC5`・`DC6`・`DC3` | 案 N10 のまま |
| `N11` | プランの雛形のステップ（SF2 から分けた） | 9 | 12 | `SF2`・`SF1a`・`SF1b`・`RF5a`・`RF1b` | SF2 が受けた Issue のうち雛形のステップ固有の 10 件のうち 9 件を分けた（#3023 は直す場所が test-run.py のため SF2 に残した） |
| `N12` | テストの同時実行の枠（#2711） | 1 | 18 | `SF1b` | 案 N9 の #2711 を 1 本にした（触るファイル 18 本で、テストの直しでなく新しい仕組みのため） |

先に要るものは、機能として先に要る子 Issue（止まった理由の表の `SF1a`、行き先の `SF2` など）と、触るファイルが重なり先に流す子 Issue である。兆候だけを直す `A3a`〜`A3g` と語だけを替える `W1`〜`W8` は、新しい子の後に流す（決定 70）。順番は頭の詳細設計の「流す順序」が持つ。

## 子 Issue ごとの設計

各子 Issue の中身と、閉じる Issue ごとの直し方である。関数とファイルは詳細設計にある。

### N1: キューの再開と参照の解決

**プランとループの 1 回が今の起動の外にある成果（版・記録の PR・前のステージの PR・検査の件数・問いの当て先・再開の位置・設計の起点）を、流す直前に参照の部品で解決し、解決できなければ種類つきで止まる。**

同時に走りうる処理: 同じ done のキューの打ち直しと `--fresh` が同時に走りうるため、`--fresh` は耐久の記録の lock を掴めたときだけ退避する。参照の解決は読むだけで、プランのファイルを書き換えないため同時に流れるキューと取り合わない（版の取り合いは今の release_version の判定のまま）

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1592 | supervise.py のキューを頭から流し直すとき、状態ディレクトリを退避しても dbos の queue-*/review-* の記録から途中のステップへ戻る | キューを同じ並びのまま頭から流し直す `supervise.py queue --fresh <done>` を足し、状態ディレクトリと、そのキューの `queue-<鍵>` と配下のプランの `review-<鍵>` の耐久の記録をまとめて退避する |
| #2344 | プランは値でなく参照を持ち、実行の直前に解決して照合する | 版・記録の PR を値でなく参照で持ち、ステップの直前に `lib/refs.py` で解決し、解決の値と出所を進捗ログへ残す。実行条件を参照より先に評価し、完了したプランをプランのハッシュで照らして流し直さない |
| #2408 | まとめのプランの release-verify が、リリース記録の番号が実行の時点で 0 になっても流れて ok で終わり、記録の無いまま「リリース後テスト」の印が付く | まとめのプランの `release-verify` を、記録の PR の参照が 0（本番の記録なし）に解決されたときは流さず「飛ばした」で残す（ステップを置く判断を組む時点から流す直前へ移す） |
| #2489 | 出し直しのループの 1 回の設計プランが、設計 PR の最新から始まらず指摘も設計の指示へ渡さない | 出し直しのループの 1 回の設計のプランに `起点のコミット: origin/design/issue-<番号>` を書き、既存の worktree を設計 PR の head へ fast-forward してから流す（指摘を設計の指示へ渡す部分は `sprint_revise.with_revise_note` に既にある） |
| #2528 | 開発ループ: 前のループの 1 回でマージ済みの設計を、次のループの 1 回の loop sprint が設計からやり直す | `lib/sprint_loop_argv.py` の `approved_issues` が、記録の承認の行に加えて設計 PR のマージの状態（ラベル design-approved つきでマージ済み）を GitHub から引き、マージ済みの課題を `--designed` へ入れる |
| #2568 | リリースだけのループの 1 回で、リリースプランの record が {queue_prs} を置き換えずに落ち、まとめのプランが流されない | `{queue_prs}` を、今の queue の items と計画の課題を閉じる PR の和で、先頭のステージと `supervise.py run` でも解決し、残ったまま打たない |
| #2569 | 検査のプランを途中から流し直すと、検査の記録の行に前の実行のレビューの件数が入らず、stats の findings が null になる | ステップの件数（counts）を進捗ログのステップの行にも書き、`check-trigger.py record` の `findings_of` が同じ計画の頭からの実行の区間の最後の件数を進捗ログから読む |
| #2638 | 開発ループ: 保留のループの 1 回を締め直す途中で出た問いを loop hold-question が「進行中のループの 1 回が無い」で断り、答えを当てる場所が無くなる | `loop hold-question` が `--plan` の属するループの 1 回（進行中か締め直しの途中の保留のループの 1 回）を引いて質問待ちへ戻し、戻した後に届いた `loop finish` を断る |
| #2648 | carul の根本原因 B: ループの 1 回とプランが「今の起動」の外にある成果（差し込みの値・検査の件数・問いの当て先・再開の位置）を参照せず、締め直しと再開で食い違う | 傘の Issue。#2663・#2664・#2665 の 3 本をこの子で閉じたら閉じる（本文の「直しても残るもの」は #2639 の 2 つ目だけ） |
| #2663 | carul の根本原因 B-1: プランの差し込みの値と検査の件数を、今の起動の外から参照で解決する | 差し込みの値（`{queue_prs}`）と検査の件数を、今の起動の外から参照で解決する（受け入れ条件 1〜14） |
| #2664 | carul の根本原因 B-2: 問いの当て先と再開の位置を、今のループの 1 回の外から参照で解決する | 問いの当て先（保留のループの 1 回を含む）と再開の位置（名前・フルパス・相対パス、実行条件、完了の照合）を今のループの 1 回・起動の外から解決する（受け入れ条件 1〜24。本文が外した #2639 の 2 つ目と #2349 の 3 つ目は除く） |
| #2665 | carul の根本原因 B-3: 次のループの 1 回の設計の起点を、設計 PR の状態から決める | 次のループの 1 回の設計の起点（設計を飛ばすか・出し直しの起点・指摘）を設計 PR の状態から決める |
| #2806 | supervise.py queue --resume の --from が計画のパスを受け付けず「前の起動の一覧に無い」と断る | `resume_targets` の照合を `match_plan` に替え、一覧の値と同じファイルを指すフルパス・相対パスも受け、断る文に「名前かフルパスで渡す」を出す |

### N2a: worker の受け渡しと指示文

**worker への規則文と受け渡し（前景で待つ規則・結果ファイルが無いときの起こし直し・修正の手順書の届け方・PR 本文の下書きの置き場・closing keywordsの範囲）を直し、closing keywordsの形のコミットを push の前に断る子と、conductor がプランを書き換える副命令を足す。**

同時に走りうる処理: plan-edit どうしはプランのファイルを lib/locks.exclusive で直列にする。run と queue はプランを起動の時点で読むため、流れている間の書き換えは次の起動から効く。流れているプランを見分ける印は今のコードに無い（`grep -rn 'exclusive(' plugins/ndf/scripts/supervise_lib/` は 0 件）。pause の worker の起こし直しは 1 つのステップの中で直列に走る

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1734 | cross-review: 修正担当（claude -p）がテストを背景で起動して通知を待ったまま終わり、結果ファイルを書かない | WORK_SYSTEM に前景で待つ規則を足し、pause の worker が結果ファイルを書かずに終わったら同じ会話を RESUME_PROMPT で起こし直す（WorkStep の full の起こし直しと同じ関数を使う） |
| #1742 | スプリントの実装のプランの pr のステップが append を持たず、worker が書いた PR の本文の下書きが載らない | 実装のプランの pr のステップに append の既定（{state_dir}/work/pr_body.md）を置き、IMPL_RULES に下書きの置き場の 1 行を足す |
| #1958 | cross-review の修正の worker が /ndf:fix の手順書を持たない最小構成で起動し、1 回 503k・16 往復を使う → 手順書を確実に読ませ、1 回の費用を実測で下げる | pause の種類ごとに届ける手順書（fix と sweep は fix の SKILL.md）を stage-skills.json の表に持たせ、_delegate_pause が種類から引いて届ける。受け入れ条件 1・3 の実測は実装の PR の本文に載せる |
| #1984 | supervise が worker の進捗ログの「止まった」の語に反応し、作業の中身（止まったものとみなす実装）でも conductor へ attention を出す | 進捗ログの 1 行に state 欄（working / failing / stopped / gate）があり、classify_worker は state と同じ文の繰り返しだけで分ける形が develop に入っている（8f4a319cf、#2337）。語の正規表現 PROGRESS_STOP は state.py に無い |
| #2005 | LLM へ渡す規則の「closing keywordsを PR 本文に書かない」が範囲を限らず、スプリント PR の「閉じる課題」の節を規則違反と読ませる | FULL_SYSTEM・PR_SYSTEM のclosing keywordsの行を「自分で書くコミットと PR 本文」に限り、スプリント PR の『閉じる課題』の節はコードが書くと添える |
| #3111 | supervise: conductor がプランを手で書き換えた差分と理由が残らず、飛ばした工程を振り返りで判定できない | supervise.py に plan-edit（理由を必須にし、控え・差分・理由・日時を進捗ログへ 1 行書く）と plan-edits（スプリントの書き換えの一覧）を足す |
| #3129 | コミットの件名「Fix: #N」: GitHub がclosing keywordsと読み、main への取り込みで課題が工程の途中に閉じる（#1271 の再発） | pr のステップが push の前に base..HEAD のコミットの件名と本文を lib/closing.py の CLOSING（`Fix: #N` の形も当たる）で照らし、当たれば push せずに止める。規則文の例を `Fix: #番号` を含む形へ直す |

### N2b: 設計の結果・受け入れ条件・重なりの判定・マージの後の作業場所

**設計の結果を読めない承認済みの課題・分割した PR・サブディレクトリの相対パスのテストで止まるか正しく判定し、受け入れ条件の節と区分をプランの中で確かめ、触るファイルの宣言の無いプランを直列にし、マージで消えた作業場所の後のステップと報告をリポジトリの根で打つ。**

同時に走りうる処理: design-results はスプリントのプランを 1 回の起動で書き換えるため、queue がそのステージのプランを流す前に走る順序（manifest のステージの並び）に頼る。acceptance-check.py record は課題ごとの錠の中で本文を読み書きする（既存）ため、同じ課題を持つ 2 本のプランが並行しても上書きし合わない

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1811 | supervise: 設計の置き場所のテストのディレクトリが重なりの判定に当たり、スプリントの実装のプランがすべて直列になる | 重なりの判定はディレクトリの宣言（末尾 /）を使わず、等しいファイルの宣言と共有の一覧だけで組を決める形が develop に入っている（73c7c5558・0be4e9482。admission.py の is_dir_decl、design_stage.py の marked_dirs）。課題の進行は完了判定まで済み |
| #2056 | design-results の書き直しが、主題で束ねた実装のプランの触るファイルと課題を先頭の課題の分だけにする | design_stage.rewrite は、プランの `課題` のうち閉じる・取り込むでない課題すべてと取り込んだ課題の `files_of` の和を `触るファイル` に書き、`課題` を落とさない形になっている（5c1b0c1bf、#3081） |
| #2165 | 設計を持たない実装のプランが触るファイルを宣言せず、同時に流す別のプランと重なるとそのファイルを「変えない」と指示されて止まる | 触るファイルを宣言しない実装のプランを、同じステージのどのプランとも重なるものとして直列にし、キューの記録に「宣言なし」の組を出す |
| #2246 | 要求の工程が、## 受け入れ条件 の節を持たない本文を通す（承認後の new sprint で初めて落ちる） | 受け入れ条件の節の判定を lib/acceptance.py の 1 関数にし、sprint_inputs.body_lack と spec-copy.py check が共有する。要求のステップの後に requirements-check をもう一度通す |
| #2274 | 取り込み（absorb）の後、取り込まれた課題の受け入れ条件の一部が取り込み先の実装から落ちたまま完了になる | 設計の結果に読めなかった課題（unread）があれば実装へ進めず止め、実装のプランの完了判定で取り込んだ課題を含む `課題` のすべての受け入れ条件の区分を求める（取り込まれた課題の設計文書は #2453 の docs_of が番号でつながる文書として載せる） |
| #2357 | 受け入れ条件の区分と退行しない条件の前提の確認が、雛形のプランで流すスプリントで書かれない | 雛形の実装のプランへ受け入れ条件の区分のステップ（worker が acceptance-check.py record を打ち、run のステップが区分の欠けを落とす）を足し、要求の検査（spec-copy.py check）が退行しない条件の前提の確認の行を確かめる |
| #2453 | 設計の結果の触るファイルが分割した設計文書へのリンクを辿らず、実装のプランから変更対象が抜ける | 分割した設計文書を番号でつなぐ docs_of・base_docs と、リンクで委ねたセルを止める DesignCellError が develop に入っている（lib/design_results.py・supervise_lib/design_stage.py）。課題の進行は確定仕様化・後片付けまで済み |
| #2539 | 設計で実装を複数の Pull Request に分けると決めても、スプリントの実装のプランは課題ごとに 1 本で組まれ、食い違いで止まらない | 設計の結果の表で 1 つの課題に PR の単位（触るファイルのセルの先頭の括弧の名前）が 2 つ以上あれば、実装のプランを 1 本で組まずに設計の結果のステージで止める（または単位ごとにプランを組む） |
| #2553 | 完了判定の設計とテストの照合が、サブディレクトリからの相対パスで書いたテストを実在するのに無いと判定して止まる | design-match.py の _path_present が、根からのパスで見つからない `/` を含む名前を末尾の一致（`*/<名前>`）でも探す |
| #2596 | 開発ループ: 承認した設計の結果を読めないと、その課題の実装を外したまま ok でリリースへ進む | 設計の結果を読めなかった設計の課題があるとき ok を返さず、cause 付きの stopped で止める（設計 PR のマージを待って打ち直す行き先） |
| #2969 | 検査のプラン: マージで worktree の登録が外れ、続く record のステップが not a git repository で落ちる | 作業場所がディレクトリとして残っていても git のworktreeとして使えなければ、Engine.keep_cwd がリポジトリの根へ移る。record のステップはこの cwd で `--root .` を打つ |
| #3022 | 検査のプランで merge の後の record が片付いた worktree の中で落ち、マージ済みなのに「止まった」になる | #2969 と同じ直しで record と abort がリポジトリの根で打たれる（今の keep_cwd は is_dir だけを見るため、登録の外れた worktree のディレクトリが残ると移らない） |
| #3186 | supervise.py: merge で終わるプランの報告で、消えた作業場所のまま gh を打ち PR 本文の通した工程を書き直せない | Engine.report の冒頭で keep_cwd を呼び、refresh_mode_line の gh をリポジトリの根で打つ |

### N3: 並列の計画の状態とレーンの生存

**並列の計画で、止めた計画を同じ名前で再開でき、レーンの生死を一覧の 1 行ではなく開発ループの記録・ラッパーの認証の止まり・止めた後の確かめと照らして決める。**

同時に走りうる処理: 耐久ワークフローの見回り・オーガナイザーの parallel.py stop/start・レーンの request が同じ台帳へ追記する。追記は既存の append_ledger_row の排他（locks.exclusive）だけで取り、start は既存の open_parallel_record の記録の排他で run を 1 本に保つ。

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #2477 | 並列の計画: ループを終えたレーンがオーガナイザーから running に見え、stall の問いも出ない | 開発ループの記録が waiting_only 以外で止まったのに一覧が running のレーンを、区切りを待たずに停滞の問いにする（閉じた PR #2919 の parallel_watch.ask_loop_stops を今の develop の上へ作り直す） |
| #2584 | 並列の計画: レーンが新しい版で送った依頼が、古い版のオーガナイザーの wait に届かない | コードの変更は要らない。台帳の行の版の違い・知らない形の行・台帳に無い待ちを wait の metrics に出す処理（parallel_ledger.ledger_report）は #2647 の PR #2886 で develop にある |
| #2617 | 並列の計画: 耐久ワークフローを作り直すと保留中の依頼と問いを失い、その後の答えを黙って捨てる | コードの変更は要らない。作り直しで依頼と問いを台帳から戻す処理（parallel_ledger.restore・RunState.adopt）と、知らない依頼への答えの保留は develop にある |
| #2637 | 並列の計画: オーガナイザーのセッションを替えると organizer の印が付かず、同時に動かす数に数えられてレーンの起動が断られる | コードの変更は要らない。start が動いている run でも印を付け直す処理（parallel_cli の mark_organizer）と、前のセッションの印を外す処理（relay_lib/hub_client._clear_organizers）は develop にある |
| #2647 | carul の根本原因 A: 計画の状態（依頼・問い・役割・使う版）が計画より短命な入れ物にしか無く、再起動・作り直し・版の更新で消える | コードの変更は要らない。計画の台帳（lib/parallel_ledger.py）と台帳からの作り直しは PR #2886 で develop にマージ済みで、進行は「デプロイ」まで済んでいる |
| #2649 | carul の根本原因 C: レーンの生死を中継の一覧の 1 行（state・at）だけで決め、一覧の成否・起動の回・起動後の生存を照合しない | 見回りの判定（一時レーンの今の起動・停滞の問いの経過と取り下げ・開発ループの止まり）を新しい判断のモジュールへ置き、閉じた PR #2919（ブランチ sprint/lcore-13、develop の 974 コミット前から分かれた）の中身を今の develop の上で作り直す |
| #2808 | 並列の計画: レーンを安全に止め、止めた計画を同じ名前で再開する手段が無い | parallel.py stop を既定で区切り（周の境・プランの開始の前）で止める形にし、止めた時点の各レーンの続きの位置を台帳へ書き、同じ計画名の start が止めた計画を台帳から作り直して続ける |
| #2936 | 並列の計画: レーンの起動・起こし直し・誤報の処理をオーガナイザーが中継し、token の 4 割をそれに使う（耐久ワークフローへ移す） | コードの変更は要らない。一時レーンの起動・起こし直し・立ち上がりの確かめを耐久ワークフローが打つ処理（lib/parallel_launch.py の launch_once・relaunch・launch_wakes）は develop にあり、進行は「デプロイ」まで済んでいる |
| #3173 | 並列の計画: 終わった 1 スプリントのレーンのラッパーが止まらず残り、動いていないとの問いが人へ届く | relay.py stop が tmux のセッションとラッパーのプロセスの終わりを確かめてから止まったと返し、耐久ワークフローの step_halt がその結果で止まったかを決める。終わりの印の無い古い版のレーンは、開発ループかスプリントの記録が終わっていれば終わったと数える |
| #3243 | 並列の計画: 認証の一時的な失敗で /goal が外れたレーンを running のまま扱い、止まりを検知しない | ラッパーの記録の「認証に失敗し替えられるアカウントが無い」の行を relay.py ls の行の止まりの理由として出し、耐久ワークフローがそれを停滞として扱い、アカウントが使えれば起こし直し、使えなければ stall の問いにする |

### N4: 中継・hook の転送・依存の環境

**中継（relay）の Stop の hook・区間の切り替え・リレーハブの起動と起こし直し・アカウントの指定・PATH の入口と、共有の依存の環境の python の選び方を、事実で判定し失敗を返す形にそろえる。**

同時に走りうる処理: Stop の hook と区間の tick はラッパーごとの作業ディレクトリのファイルを読み書きし、別のラッパーとは置き場が分かれる。リレーハブの restart は受付箱を 1 本ずつ処理する今の形のまま上限の判定と停止と起動を 1 回の処理の中で行う。共有の依存の環境は置き場の名前に python の版を含めることで、別の python での作り直しが走っているプロセスの環境と重ならない（同じ python どうしの uv sync は uv の置き場の排他に任せる）。

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1877 | relay: /goal の続きの応答では stop_hook_active が真になり、背景の作業が残る ndf-next の応答で Stop を止めて知らせる仕組みが働かない | hold_once から stop_hook_active の条件を外し、同じ区間・同じ候補で 1 度だけ止める判定（held.json）だけで繰り返しを防ぐ |
| #1919 | conductor が NDF のスクリプトをシェルでつないで待つと、hook が止めてプランと wait を案内する | PreToolUse の hook が、背景の Bash でも NDF のスクリプトを 2 つ以上 && / ; でつないだコマンドと sleep の後に NDF のスクリプトを続けるコマンドを拒み、プランのキューと supervise.py wait を案内する |
| #2324 | 中継の Stop の hook が、別のセッションの承認待ちを報告しただけの応答を「本文で承認を待って終わった」と差し戻す | prose_wait_reason が本文の語だけで決めず、背景の処理の待ち・このセッションの判断の記録・別の場所での答えの案内を差し戻さない材料にし、差し戻した応答の要約と材料をラッパーの記録へ残す |
| #2438 | relay.py: /goal の目標が未達のカットポイントで、ndf-next を出しても次の区間へ切り替わらない | _tick が切り替えない理由を毎回ラッパーの記録へ書き、目標の未達の判定をシグナルファイルの書き直しの時刻ではなく同じ候補を最初に書いた時刻と比べる |
| #2461 | install-wrapper が PATH 上に ndf-relay コマンドを置き、relay.py をどこからでも呼べるようにする | install-wrapper が ~/.local/bin/ndf-relay（~/.claude/ndf/relay.py を呼ぶだけ）を置き、uninstall で消し、status で有無を出す |
| #2464 | relay.py account use の指定を supervise.py の子の claude -p が読まない | アカウントの指定（pinned.json）の読み出しを relay_lib/pin.py から lib/claude_accounts.py へ移し、choose が指定を先に見るようにして、supervise の子の claude -p も指定に従う |
| #2504 | 中継: tmux に起動するレーンの claude で Prompt suggestions を止める | リレーハブが tmux に起動するラッパーの環境へ CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false を足す（子の claude はラッパーの環境を継ぐため、アカウントの切り替えの後の区間にも効く） |
| #2563 | 中継: relay.py spawn・restart が「起動した」を返すのに、ラッパーが立ち上がっていない | 期待の 1 つ目はコミット 4aaf8046a で入った。残る期待の 2 つ目（同じ名前の止まったラッパーがあっても起動できる）をテストで縛り、再現を通して閉じる |
| #2583 | relay: プラグインの更新でリレーハブの版のラッパーの置き場が消え、レーンの起こし直しが何も出さずに失敗する | コードの変更は要らない。片付けが使用中の印のある置き場を消さない処理（version_dir.prune）と、置き場が消えたら relay.current の版で起こす処理（hub.launch の launch_python）は develop にある |
| #2753 | relay.py restart が止めた後に上限で断り、レーンが止まったまま残る | restart の停止をリレーハブの中へ移し、上限の判定で対象のレーンを数えずに通ったときだけ止めて起こす。断るときは止めない |
| #3105 | 共有の依存の環境: python の選び方が呼び出しで食い違い、走っているプロセスの最中に環境が作り直されて import が落ちる | 共有の依存の環境を作る python の選び方を lib/deps.py の 1 つの関数にし、deps の uv run・ラッパーの uv sync・hook-env の uv sync が同じ --python を渡す。環境の置き場の名前に python の版を含め、別の python の環境を同じ置き場に作り直さない |

### N5: cross-refactoring の振る舞い

**cross-refactoring の振る舞いの不具合 8 件を直す。足したテストが全体の実行に入るかの確かめ・提案の範囲を課題が触る関数へ絞る・読み取りの担当の結果ファイルの置き場・drive の案内・着手前のテストのパスの種類・雛形の試走の上限・最終ゲートの範囲外の直し・項目の範囲テストの絞り込み。**

同時に走りうる処理: 提案・リファクタリング計画・判定は担当ごとに並列に走る。結果ファイルを担当ごとの読み取り用ディレクトリへ移すため、書く先は重ならない。作業ディレクトリ（work/）を書き換える手順は今どおり 1 つずつ走り、実行の持ち主（run_owner）が排他を取る

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #620 | cross-refactoring: テストを足す項目（add-tests）が pytest の収集しないファイルへテストを足し、実行されないまま検証を通る | add-tests の確かめで、足したテストのファイルが宣言の suite の全体の実行に収集されるかを test_strategy.py から確かめ、収集されなければ項目を差し戻す |
| #622 | cross-refactoring の --scope がファイル単位のため、issue と関係の無い既存の関数まで整理し、変わった範囲が issue から読めない | 提案の範囲を PR の差分が触る関数・シンボルに絞り、外の既存の関数への提案は見送り（範囲外）にする。検査のプランの 2 経路も cross-refactoring の側で差分から関数を引くため同じ範囲になる |
| #750 | cross-refactoring: 提案担当の codex が結果ファイルを提出先に書けず NO_RESULT になる（提出先が読み取り用ディレクトリの外） | propose・plan・judge の結果ファイルの置き場を担当の読み取り用ディレクトリの中（.cross_refactoring/）へ移し、終わった後に起動層が一時ディレクトリへ回収する |
| #2057 | cross-refactoring の drive が完了した回を返す案内に、持たない --reopen を書く | loop_drive.keep_finished_output が案内に出す打ち直しの手段を呼ぶ側の drive から受け取り、cross-refactoring の drive は --reopen を書かない |
| #2096 | cross-refactoring: 全体テストを CI に任せる戦略の着手前のテストがディレクトリを {paths} へ入れ、ファイルだけを受けるテンプレートで落ちる | CI に任せる戦略の着手前の範囲テストへ、テストの置き場所（ディレクトリ）ではなくその配下のテストのファイルを渡す |
| #2158 | cross-refactoring: 着手前のテストが上限の近くで通ると、範囲テストのテンプレートの試走の残り時間が数秒になり init が止まる | 雛形の試走の上限に、着手前のテストの所要から独立した下限を持たせる |
| #3038 | cross-refactoring: 最終ゲートが全体テストの失敗を直すためにリファクタリング計画の対象範囲の外のファイルを変える | 最終ゲートの直しがリファクタリング計画の対象範囲の外のファイルを変えたら取り込まず、失敗を範囲外として記録して止まる。範囲が空のときに検査を飛ばす分岐をなくす |
| #3049 | cross-refactoring: 範囲テストを触ったモジュールに対応するテストへ絞り、実質の全体テストにしない | 項目の範囲テストの対象を、触ったソースのモジュールを import するテストのファイルへ絞る。ディレクトリを対象に受けない。引けないときは最終ゲートへ寄せて記録する |

### N6: cross-review の同期と worktree

**cross-review の対象リポジトリの決め方・PR のブランチとレビュー用のworktreeの同期・巻き直しの後の番号の規則・作業中のworktreeの登録の保護を、事実で決めて理由を返す形にそろえる。**

同時に走りうる処理: 人や別のレーンが同じ PR のブランチへ push するのと、収束ループの修正の push が重なる。強制の push はせず、拒まれたら取り込んで 1 回だけ送り直す。別のコンテナや同じリポジトリの別のセッションの git worktree prune とレビュー用のworktreeが重なるのは、作った直後の lock で防ぐ（lock は git の管理情報のファイルで、別のマウント名前空間からも見える）。

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1707 | cross-review: ai-plugins 以外のリポジトリで SKILL.md のとおりに単独起動すると、Skill のディレクトリが git リポジトリでないため PR を取れずに止まる | 対象のリポジトリを --repo・状態ファイル・作業ディレクトリの origin の順で決め、作業ディレクトリが git リポジトリでなく他の手がかりも無ければ「対象のリポジトリを決められない（対象のリポジトリで打つか --repo を渡す）」で止める。SKILL.md の起動の 1 行を対象のリポジトリで打つ形（resolve.sh scripts cross-review の入口）に直す |
| #2440 | cross-review: 修正中に PR のブランチへ別の push が入ると止まり、流し直しても同じ状態から止まる | 修正の push が non-fast-forward で拒まれたら、origin の先頭を取り込んで自分の未送信のコミットを載せ直す取り込みを 1 回試みてから送り直す。流し直しの初めに、レビュー用のworktreeを origin の先頭へ合わせ自分の未送信のコミットだけを載せ直す |
| #2770 | cross-review の駆動が、PR の巻き直しの後と駆動の外のコミットの後に修正の結果を取り込まない | 巻き直しの後も状態ファイル・結果ファイル・修正担当への指示を元の PR 番号の 1 つの規則でそろえ、結果ファイルの中の pr は元の番号と今の番号のどちらも受ける。start-round が修正の結果ファイルの残りを見つけたら取り込んでから次のラウンドへ進む |
| #3106 | cross-review: 作業中の /tmp の worktree の登録が消え、取り込みが push できずに止まる | レビュー用のworktreeを作った直後に git worktree lock を掛け、片付け（merged-steps.py の remove_worktree・lib/run_state.py）が外す前に unlock する。prune は lock した登録を消さない（手元で確かめた） |
| #3109 | クロスレビュー: レビュー中に PR の worktree の管理情報が消え、result_posts.py fix が「リポジトリを決められない」で止まる | result_posts.py fix がリポジトリを引く前にworktreeの管理情報（git rev-parse --git-dir）を確かめ、無ければ「worktreeの管理情報が無い（<パス>）」を理由に止める。fix-steps.py の結果とログの既定の置き場を /tmp からworktreeの .cross_review へ移す |

### N7: 棚卸し（backlog-refinement）

**棚卸し（backlog-refinement）の候補・持ち越し・却下・切り出しの境界・見直しの上限と採点の欠けの不具合 6 件を直す。**

同時に走りうる処理: 複数の worktree とセッションがメインディレクトリの同じ .ndf/backlog/ を読む。見直し・大整理は今の持ち主の宣言（owner.json の錠）で 1 つずつ走る。candidates.json・rejected.json・estimates.json は jsonio.write_atomic で書き、apply は locks.exclusive で状態のディレクトリを囲む

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1595 | backlog-refinement: apply が plan の rejected を依存先として一致した別の候補の値で記録し、却下が効かない | plan の却下を照合するとき本人の候補を先に採り、本人の候補が無く依存先にしか無い番号は plan の誤りとして返す。前倒しを全部退けても後ろ倒しの却下を古くなった記録として消さない |
| #1640 | 棚卸しで上限を超えて持ち越した候補が、次のスプリントの棚卸しへ渡らない | upkeep の状態（候補・却下・見積・持ち越し）の置き場を一時ディレクトリからメインディレクトリの .ndf/backlog/ へ移し、candidates が前回の持ち越しを経路 carried で拾う |
| #2064 | 棚卸しの candidates が --add で渡した課題も上限で切り、利用者が指定した対象が持ち越しへ回る | 候補を上限で切る並びで、--add で渡した課題（経路 manual）を commit-subject より先に残す |
| #2232 | 棚卸しの切り出しの境界が人の判断待ちの行を容量に数える | 切り出しの境界へ渡す行から人の判断待ちの行を除く |
| #2423 | 棚卸しの見直しのステップに費用と秒の上限が効かない | 見直し・大整理の work ステップの打ち切りを depths.<深さ>.seconds から読み、効かない usd を既定から外す。打ち切ったときに持ち越しを depths.jsonl へ書く |
| #2540 | backlog-refinement: 見直しが見積の欠けた課題を残したまま終わり、持ち越しで次の見直しがすぐに起動する | 見直しの順位の算出で、記録の要約値が今の課題と違う見積と前回の表から戻した見積を excluded（採点し直す）で返す |

### N8a: 工程の終わりの記録・引継ぎ文書・用語集

**工程の終わりの記録（確定仕様化・後片付けの工程表・リリース後テストの合否）、引継ぎ文書の大きさと実在の確かめ、用語集の分割の出力を直す。**

同時に走りうる処理: SubagentStop の hook は別々のサブエージェントで同時に走る。同じ agent_id の差を書くときは帳簿のファイルの錠（locks.append_locked と同じ錠）の中で既存の行を読んでから書く。後片付けは並列のレーンから同時に打たれうるため、課題の本文の更新は progress-record.sh の側の 1 回の取得と更新に任せ、記録済みを見て打ち直さない。MVV の記録は追記だけ

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1616 | glossary.py render: docs/glossary.md を複数ファイルへ分割して出せるようにし、行数検査の EXEMPT を外す | glossary.py render がコンテキストごとのファイルと索引（docs/glossary.md）を出し、check-doc-line-limit.py の EXEMPT から docs/glossary.md を外す |
| #1715 | plan-to-spec: spec-finalize が --spec を 1 つしか受けず、既存の複数ファイルの確定仕様へ溶かすと残りのファイルがコミットに入らないまま ok で終わる | spec-finalize の --spec を複数受け、すべてをコミットへ入れる。用語の出所は語ごとに定義を含むファイルを選び、選べなければ止まる |
| #1785 | 引き継ぎ文書に大きさの上限が無く、milestone-26.md が 52KB に育って conductor が区間の頭で毎回 3 万文字を読み込む | handoff.py check にバイト数の上限を足し、--trim が雛形に無い節も退避先へ移す。parallel-<計画名> の名も検査の対象にする |
| #1835 | 引継ぎ文書に「作成済み」と書いたファイルが実在しなくても、書く時点にも読む時点にも気付けない | handoff.py check が本文に書いたリポジトリの中のパスと既知の状態の置き場のパスの実在を確かめ、無いものを一覧で返す |
| #1893 | 後片付けを終えても課題の工程表の「後片付け」の行が未チェックのまま残る | merged-steps.py の後片付けが、PR が閉じる課題ごとに工程「後片付け」を記録する。記録済みの課題には打ち直さない |
| #2293 | sprint-close: --with-verification を付け忘れると、リリース後テストに不合格の行を持つ課題も閉じる | 記録の PR にリリース後テストの記録があれば、--with-verification の有無によらず閉じる条件 2 を見る |

### N8b: 使用量の帳簿と MVV の記録

**使用量の帳簿（日付の区切り・同じ応答の数え方・置き場・停止の hook の順序）と MVV の記録（worktree の判定・判定の再利用・覆しの数え方）を直す。**

同時に走りうる処理: SubagentStop の hook は別々のサブエージェントで同時に走る。同じ agent_id の差を書くときは帳簿のファイルの錠（locks.append_locked と同じ錠）の中で既存の行を読んでから書く。後片付けは並列のレーンから同時に打たれうるため、課題の本文の更新は progress-record.sh の側の 1 回の取得と更新に任せ、記録済みを見て打ち直さない。MVV の記録は追記だけ

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1978 | usage-report.py の閉じた課題の数が UTC の日付で数えられ、使用量の集計（JST の日付）と期間がずれる → JST の両端で数える | 閉じた課題の数を JST の日付の両端（<since>T00:00:00+09:00..<until>T23:59:59+09:00）で検索する |
| #1979 | 使用量の帳簿が同じ message.id の最後の usage を捨て、続けたサブエージェントを 2 回数え、日をまたぐ会話を丸ごと採るか落とす → 会話の記録の合計と一致させる | 会話の記録の読みを message.id ごとの最後の行の使用量にし、同じ agent_id の 2 回目以降の停止は差だけを書き、期間の集計は応答ごとの時刻で絞る。runtime: claude の work の行を報告に 1 回だけ載せる |
| #2575 | 使用量の帳簿が書き手ごとに別の置き場へ書かれ、サブエージェントの行と claude -p の行を同じ集計で読めない | 帳簿のディレクトリの決め方から CLAUDE_PLUGIN_DATA を外し、hook とスクリプトが同じ置き場（NDF_USAGE_DIR → 状態のホームの usage）へ書く |
| #2576 | サブエージェントの停止の hook が最後の応答を読む前に帳簿を書き、使用量を最後の応答 1 件分少なく数える | 集計の側で、サブエージェントの帳簿の行の和と会話の記録の合計の差を会話の記録から補う。hook の側は #1979 の差を書く形で次の停止が抜けを拾う |
| #2603 | MVV 改訂の兆候の集計が、worktree で打った判定の記録を数えず「判定できない」を 0 件と出す | MVV の記録の repo を書く側でメインディレクトリへ解く（mvv_repo_key）。worktree から打った判定も同じリポジトリとして数える |
| #2614 | スプリント MVV の照合が同じ本文を毎回 LLM で判定し直し、判定の揺れで開発ループが止まる |  |
| #2777 | MVV の改訂の兆候が、「判定できない」で人へ戻して人が通した承認を「覆し」と数え、改訂を求め続ける | MVV 判定が「判定できない」で人が通した承認を覆しに数えず、別の数（unknown_pass）にする。同じスプリント状態・承認ゲート・PR の覆しは 1 件にまとめる |

### N9: テストの揺れ・残骸・隔離・後始末

**テストの揺れ（壁時計・並列・パスの形・起動の順）を待たなかった場合だけ落ちる形へ直し、本物の状態の置き場からテストを隔離し、残骸と重複のテストを削り、親ごと消えた worktree のテスト環境の割り当てを回収する。**

同時に走りうる処理: テストは pytest -n 4 と CI の 2 シャードで並列に走る。_forbid_real_state は各ワーカーの一時ディレクトリへ向けるため取り合わない。回収（reclaim）は今の錠（RECLAIM の lock）のまま

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1735 | test_external_ai_run の codex の成功ケースが CI でだけまれに落ちる | external-ai.py run の子プロセスのケース（codex・kiro・agy）が CI でだけ落ちる原因を、assert の出力（結果 JSON の summary）と agy の `launch_failed`・`pid ... cmdline does not ...` から特定し、偽の CLI の起動の確かめを起動の順に依らない形にする |
| #1967 | test_relay.py の test_guard_* が並列の全体テストでだけ落ち、全体テストを使う工程が揺れで止まる | `test_guard_exit_queued_behind_question` の `time.sleep(2.5)` と `NDF_RELAY_TERM_WAIT=1` の組のように壁時計で SIGTERM の有無を見る確かめを、シグナルファイルのファイルを待つ形へ替える |
| #2130 | 全角の文字を含むパスに置いた worktree で、コマンドの文字列を照合する 2 件のテストが落ちる | コマンドの文字列を照合する 2 件の期待値を、shlex.split で分けた語の並びで比べる形にする |
| #2758 | 壁時計の短い上限を持つテストが高負荷の全件テストで落ち、プランを止める | 壁時計の短い上限（time.time() - x < N）を持つ 17 件のテストの上限を、待たなかった場合にかかる時間に合わせる（待つ側を長くして打ち切り・早い戻りの意図を保つ） |
| #2776 | テストの入力 `{壊れた行` が本物の差し戻しの台帳へ上書きで書かれ、それより前の記録が消えていた | テストの共通の前提（tests/conftest.py）が本物の状態の根（~/.claude/ndf/state・~/.local/state/ndf）への書き込みを落とす形にし、`g2_ledger` が台帳のパスが一時ディレクトリの下かを確かめる |
| #2798 | cross-refactoring の worktree が親ごと消えると、テスト環境の割り当てが回収されずスロットを占め続ける | `worktree-testenv-sweep.sh` の `_reclaim_location_seen` が、親のディレクトリが無いとき最も近い残っている祖先でファイルシステムを確かめ、同じなら stale として回収する |
| #2964 | テストの前提作りの重複と固定の待ちを除く: 雛形の sleep 5・通過記録の積み上げ・reclaim の照合・収集の 3 回（テストの所要 32〜35 秒減の見込み） | 雛形の sleep 5 をテストの側で差し替え、通過記録を 1 度に置くフィクスチャを足し、`_reclaim_check_owner` の照合を読み込んだ値を受ける関数に分け、`_collected_ids` の収集を 1 回にする |
| #2974 | テスト: 消えた機能の残骸・検査スクリプトと同じ確かめ・文言の固定・総当たりを削る（#2309 の残り） | 消えた機能が無いことだけを確かめるテスト（B）・構造の検査と同じ 500 行の確かめ（C）・文言と行数の固定（D）を消し、総当たり（E）を代表 1〜3 件へ縮める |
| #3140 | テスト: test_peak_measures_each_count_of_runs が同時に流す 2 本の起動の順で落ち、文書だけの PR の CI を止める | `test_peak_measures_each_count_of_runs` の `started` の比べ方を、同時に流す回の中の順に依らない形（回ごとの集まりで比べる）にする |

### N10: 監視・外部 AI・マージの hook

**外部 CLI の監視の照合の範囲と警告の出し方、1 回の実行を識別子 1 つで追う記録（#2338 の設計 PR #2497 のとおり）、マージの hook の対象リポジトリの決め方、kiro の起動がworktreeを書き換えないことを直す。**

同時に走りうる処理: 子の実行が親と同じ記録のファイルへ並行に書く。書き口のファイルの出力先が排他（fcntl.flock）の中で書きかけの行を閉じてから 1 行を書き切る（#2338 の設計の決定 1・6）。別のコンテナは自分の起点の識別子のファイルにだけ書くため、同じファイルを取り合わない。

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1411 | cross-review の監視が、err.log に写った既存コメントの「quota exceeded」を codex の利用上限と誤判定して中断する | 利用上限の一般語（quota exceeded・rate limit exceeded）を、CLI が自分で出す行の形（行頭の ERROR: か HTTP の状態行）に限り、プロンプトの写しの中の平文を照合しない |
| #2059 | 監視が先に別の早期の警告を出していると、非致命にした permission_denials の件数を警告に残さない | 監視のループの早期の警告を種類（標準エラーの記録・permission_denials）ごとに 1 回ずつ出す（#2338 の設計の Pull Request 2） |
| #2338 | 1 回の実行を識別子 1 つで追える記録とログにそろえる | 書き口 lib/run_log.py を新設し、実行の核（lib/run_core.py）から始まり・判断・診断・終わりを実行の記録へ書き、monitor_loop.py と check-trigger.py の標準エラーへの直書きと先頭 N 字の切り詰めを書き口へ移す（設計 issues/issue-2338-design.md、マージ済みの設計 PR #2497） |
| #2446 | マージを判定するフックが gh pr merge の --repo を見ず、作業ディレクトリのリポジトリで PR を引く | マージの hook の wf_merge_target が -R / --repo と REST の repos/<所有者>/<名前>/pulls/ の部分から対象のリポジトリを取り出し、wf_check_merge がその slug で PR を引く。対象が作業ディレクトリと違えば本番チャネルの判定に作業ディレクトリの宣言を使わず、対象の宣言を読めなければ止める |
| #2448 | cross 系で kiro を起動すると、worktreeの .amazonq/cli-agents/*.json を書き換えて .bak を残す | kiro の起動（scripts/lib/launch-cli.sh の run_runtime の kiro の枝）の前後でworktreeの .amazonq/cli-agents を写しておき、起動の後に追跡対象の変更を戻し、起動の間に増えた .bak を消す |

### N11: プランの雛形のステップ（SF2 から分けた）

**SF2 が `on_cause` で書き直したプランの雛形に、検査・リリース・閉じ・実装のプランのステップごとの不具合 9 件の直しを足す。**

同時に走りうる処理: 雛形はプランを組む時点の純粋な組み立てで、同時に走る処理と取り合うものは無い。#2140・#3108 の直しで verify がメインディレクトリへ書かなくなり、並べて動く別のスプリントのメインディレクトリの変更と取り合わなくなる

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #1502 | mvv-gate: 設計のプランで、設計の前に打った requirements-check の exit 2 を材料に読み、設計承認ゲート の助言が unknown になる | `pr.py` の `_machine_body` が、行き先で分岐した非 0（要求の写しが無い requirements-check の 2）を失敗として載せず、後の requirements-recheck の結果で置き換える |
| #2049 | 検査のプランのリファクタリングが --sync-command を渡さず、生成元に触れる改善が push で止まる | 検査のプランの refactor のステップが、`.ndf/supervise.json` の `sync_checks` の同期のコマンドを `--sync-command` で cross-refactoring の drive.py へ渡す |
| #2054 | サブモジュールのリポジトリで new fix の作業場所が .git/modules の下になる | `templates.fix_worktree` のメインディレクトリの求め方を `lib/decl.py` の `main_dir`（core.worktree を読みサブモジュールを解く）へ寄せる |
| #2118 | 実装のプランの design-tests が、設計で改名を決めたテストを止まりと判定せず、止まった理由を全文で残す | `pr.py` の run ステップの要約を 120 字で切らずに載せ、`design-match.py` の `extract_names`・`classify_span` が「採らなかった案」の中の名前と設計が宣言した改名の前の名前を missing にしない |
| #2140 | リリースのプランの verify が、メインディレクトリが本番ブランチにいると develop を fast-forward できずに止まる | リリースのプランの verify のステップがメインディレクトリで git pull せず、`verify-install` に `--root <リリースの worktree>` を渡す |
| #2370 | スプリントの検査で cross-review が未解決 0 件のまま上限や error で終わると検査ごと落ち、conductor が手で review を飛ばして再開する | 検査のプランの review のステップの行き先を、cross-review の結果（unresolved・final）の種類で分け、未解決 0 件で上限か error に当たったら次の工程へ進み、そうでなければ人へ戻す。どちらへ進んだかを検査の記録に残す |
| #3003 | 検査のプラン: 振る舞いの確認の後に head が進んでも、マージの前に確かめ直さない | 検査のプランのマージの前に、振る舞いの確認を記録した head から今の head までに振る舞いを決める文書が変わったかを見るステップを足し、変わっていれば確かめ直しへ、変わっていなければそのまま進む |
| #3108 | 開発版のデプロイ: リリース後テストの前の取り込みが、メインディレクトリに残った版のファイルの変更で落ちる | 開発版のデプロイの verify の前の取り込みを #2140 と同じ形にし（メインディレクトリで pull しない）、メインディレクトリの未コミットの変更で落ちないようにする |
| #3112 | 実装のプラン: impl の担当が全体テストを打ち、過負荷で無進捗の打ち切りになる | 実装の指示の共通の規則（`IMPL_RULES`）に「全体テストを打たない（後段の全体テストのステップが受け持つ）」を足す |

### N12: テストの同時実行の枠（#2711）

**重いテストの同時実行を作業マシンの上限（枠）に収め、メモリの下限を割ったら OOM Killer より先に NDF が待たせるか止める（#2711）。**

設計は閉じた設計 PR #2744 のブランチ `design/issue-2711` の `issues/issue-2711-design.md`（目的・構成要素・データ構造・テスト設計）を材料にする。触るファイルはその「設計の結果」の行から、ディレクトリの `plugins/ndf/scripts/tests/` を新設する 2 本のテストのファイルへ、`docs/glossary/` を `docs/glossary/glossary.json` へ絞った（ディレクトリのままだと、テストを触るすべての子 Issue と重なる）。

| Issue | 題 | 直す中身 |
| --- | --- | --- |
| #2711 | OOM Killer 対策: pytest などの重い処理がランナーの CPU とメモリを使いつぶさないようにする | テストの直しでなく、重い処理の同時実行の枠とメモリの待ち・止めの仕組みを足す機能である（files のうち test-slot.py・lib/test_slots.py・lib/test_weight.py・hook_lib/test_guard.py は設計が新設するファイル）（実装は develop にある。独立の子にする。設計は承認済み（PR 2744、ブランチ design/issue-2711 に残る。2026-11-07 に共有の置き場を /work の下にすると利用者が決めた）で、触るファイル 17 本は hook.py（DC3・W5）・lib/procs.py（DC3）・steps.py（SF2・SF6）・refactor_lib/process.py（W4）・test-run.py（SF1b）と重なり、N9 のテストの直しと流す順が合わない） |

## 決定の記録

### 決定 69: ラベルの Issue をすべて閉じるため、既にある子 Issue の範囲に入るものはその子へ足し、入らないものを主題とファイルでまとめた新しい子 Issue にする

- **決めたこと**: 子 Issue でも割り当て済みでもない 225 件のうち、本文が名指すファイルと関数が既にある子 Issue の変更の中にあるもの（112 件。頭の詳細設計で割り当てた 3 件を含む）はその子へ足した。残りの 110 件は 14 本の新しい子 Issue（N1〜N12）にまとめた
- **理由**: 既にある子が同じ関数を書き直すのに、別の子で同じ関数を直すと、同じファイルを 2 度直すことになる。範囲の外のものを 1 本にまとめると、触るファイルが 300 本を超えて流す順番の最後まで待つ
- **退けた案**: すべてを 1 本の子にまとめる案（PR が大きく、revert 1 つで戻せる単位にならない）と、Issue ごとに 1 本の子にする案（子が 110 本増え、見張りの手間が子の数に比例する）

### 決定 70: 新しい子 Issue は事実を移す子・行き先を変える子・リファクタリング計画の子の後、兆候だけを直す子と語だけを替える子の前に流す

- **決めたこと**: 優先の順で、N1〜N12 を SF13 の後、`A3a`〜`A3g`・`DC8`・`DC7`・`K2`・`SF8`・`W1`〜`W8` の前に置く
- **理由**: 新しい子は関数の中身を直す。兆候と語は、中身が決まった後に 1 度だけ直す（頭の詳細設計の「処理の手順」の「A3a〜A3g」と「W1〜W7」の理由と同じ向き）

### 決定 71: SF2 が受けた Issue のうち雛形のステップ固有の不具合を N11 へ分ける

- **決めたこと**: 下調べで SF2 へ足すとした 18 件のうち、行き先の選び方そのもの（同じ原因の打ち直しの上限・`base_conflict` の検出・変更なし・worker の関門）の 8 件と、#2349・#3023 を SF2 に残し、雛形のステップの中身（検査・リリース・閉じ・実装のプランのステップ）の 9 件を N11 にした
- **理由**: SF2 に 18 件を足すと解消する Issue が 19 件になり、目安の 15 件を超える。雛形のステップの直しは、SF2 が雛形を `on_cause` で書き直した後なら独立に流せる

### 決定 72: 実装が develop に既にある Issue は、子 Issue の PR で再現の手順を通して閉じる

- **決めたこと**: #1811・#1984・#2056・#2453・#2583・#2584・#2614・#2617・#2637・#2647・#2936 は、直しのコードが develop にある。新しい変更を作らず、受け持つ子 Issue の PR で再現の手順を 1 回通し、直った PR を添えて閉じる。リリース後テストが残るものは、子 Issue のリリース後テストで確かめる
- **理由**: ラベルの Issue は #1142 の改善で閉じることが受け入れ条件で、閉じる経路を子 Issue の PR の `Closes` の行にそろえると、閉じ忘れを子 Issue の数で数えられる

> **#3347:** 決定 69〜75 は系統 `fixes` の番号で、[issue-1142-design-next.md](issue-1142-design-next.md) の決定 73〜75（系統 `next`）とは別である。別の系統から指すときは `決定 73（fixes）` と書く（[issue-3347-design.md](issue-3347-design.md) の決定 102）。

### 決定 73: #2711（テストの同時実行の枠）は、閉じた設計 PR の設計文書を材料にして N12 として 1 本で流す

- **決めたこと**: N12 の設計は `design/issue-2711` のブランチの設計文書を材料にし、触るファイルのディレクトリの指定をファイルへ絞る
- **理由**: #2711 はテストの直しではなく新しい仕組みで、触るファイルが 18 本ある。N9（テストの揺れ・残骸）と一緒にすると、N9 が DC3・SF2・SF6・SF1b と重なって後ろへ下がる

### 決定 74: 閉じられない 6 件は子 Issue に入れず、理由を添えてオーガナイザーへ送る

- **決めたこと**: #1571・#1601・#2316・#2341・#3113・#3189 は子 Issue に入れない。理由は [issue-1142-design-labels.md](issue-1142-design-labels.md) の「閉じられない Issue」にある。ラベルを外すのは利用者の同意の後で、依頼はオーガナイザーへ送る
- **理由**: 6 件はスクリプトの作り直しでは閉じない（新しい実験版・既に直っている・未決の外部の実装・リポジトリ全体の入口の統一・実験版の直し・調査）

### 決定 75: 記録の案は、工程の終わりの記録と使用量・MVV の 2 本に分ける

- **決めたこと**: 案 N8 を N8a（確定仕様化・後片付け・リリース後テストの合否・引継ぎ文書・用語集）と N8b（使用量の帳簿・MVV の記録）に分ける
- **理由**: 1 本では主題が 7 つ・先に要る子が 18 本になる。2 本は触るファイルが重ならず、同じ順番に流せる

## テスト設計

| 受け入れ条件 | 確かめ方 | 置き場 |
| --- | --- | --- |
| ラベルの Issue が子 Issue の変更で閉じる | 子 Issue の PR ごとに、解消する Issue の再現の手順を 1 回通した結果と `Closes` の行を本文に載せる | 子 Issue の PR 本文 |
| 各 Issue の直しが働く | 詳細設計の「単体テストの観点」の行ごとに単体テストを足す。古い振る舞いを期待値に持つテストは消して作り直す | `plugins/ndf/scripts/tests/` ほか各 Skill の `tests/` |
| ラベルの Issue が残らない | E9（測り直し）と同じ時点に `gh issue list --label rebuild-1142 --state open` を打ち、閉じられない 6 件のほかに残りが無いことを確かめる | #1142 のコメント |

## 設計の結果

先頭の設計文書（[issue-1142-design.md](issue-1142-design.md) ）の「設計の結果」の表へ写す行である。

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #1142 | 実装する | — | （N1。設計承認ゲートの後に起票）`plugins/ndf/scripts/lib/refs.py`、`plugins/ndf/scripts/lib/ref_sources.py`、`plugins/ndf/scripts/supervise_lib/queue_fill.py`、`plugins/ndf/scripts/supervise_lib/queue.py`、`plugins/ndf/scripts/supervise_lib/queue_resume.py`、`plugins/ndf/scripts/supervise_lib/flow.py`、`plugins/ndf/scripts/supervise_lib/engine.py`、`plugins/ndf/scripts/supervise_lib/plan.py`、`plugins/ndf/scripts/supervise_lib/state.py`、`plugins/ndf/scripts/check-trigger.py`、`plugins/ndf/scripts/supervise_lib/sprint.py`、`plugins/ndf/scripts/supervise_lib/sprint_routes.py`、`plugins/ndf/scripts/supervise_lib/release_templates.py`、`plugins/ndf/scripts/supervise_lib/delivery_templates.py`、`plugins/ndf/scripts/supervise_lib/sprint_revise.py`、`plugins/ndf/scripts/lib/sprint_loop_argv.py`、`plugins/ndf/scripts/lib/sprint_loop.py`、`plugins/ndf/scripts/lib/sprint_loop_gate2.py`、`plugins/ndf/scripts/lib/sprint_loop_record.py`、`plugins/ndf/scripts/lib/durable.py`、`plugins/ndf/scripts/supervise.py`、`plugins/ndf/scripts/tests/test_refs.py`、`plugins/ndf/scripts/tests/test_supervise_queue_continue.py`、`plugins/ndf/scripts/tests/test_sprint_loop.py`、`plugins/ndf/scripts/tests/test_check_trigger.py`、`plugins/ndf/scripts/tests/test_release_version.py` |
| #1142 | 実装する | — | （N2a。設計承認ゲートの後に起票）`plugins/ndf/scripts/supervise_lib/prompts.py`、`plugins/ndf/scripts/supervise_lib/worker_steps.py`、`plugins/ndf/scripts/supervise_lib/steps.py`、`plugins/ndf/scripts/lib/stage_skills.py`、`plugins/ndf/scripts/lib/stage-skills.json`、`plugins/ndf/scripts/supervise_lib/claude.py`、`plugins/ndf/scripts/supervise_lib/templates.py`、`plugins/ndf/scripts/supervise_lib/verify_steps.py`、`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/lib/closing.py`、`plugins/ndf/scripts/supervise_lib/pr.py`、`plugins/ndf/scripts/supervise_lib/plan_edit.py`、`plugins/ndf/scripts/supervise.py`、`plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__supervise.py.json`、`plugins/ndf/scripts/tests/test_supervise_plan_edit.py`、`plugins/ndf/scripts/tests/test_closing.py`、`plugins/ndf/scripts/tests/test_stage_skills.py`、`plugins/ndf/scripts/tests/test_drive_refactor.py`、`plugins/ndf/scripts/tests/test_supervise_new.py` |
| #1142 | 実装する | — | （N2b。設計承認ゲートの後に起票）`plugins/ndf/scripts/supervise_lib/admission.py`、`plugins/ndf/scripts/supervise_lib/queue.py`、`plugins/ndf/scripts/supervise_lib/flow.py`、`plugins/ndf/scripts/lib/acceptance.py`、`plugins/ndf/scripts/supervise_lib/sprint_inputs.py`、`plugins/ndf/scripts/spec-copy.py`、`plugins/ndf/scripts/supervise_lib/design_steps.py`、`plugins/ndf/scripts/acceptance-check.py`、`plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__acceptance-check.py.json`、`plugins/ndf/scripts/supervise_lib/templates.py`、`plugins/ndf/scripts/supervise_lib/verify_steps.py`、`plugins/ndf/scripts/supervise_lib/design_stage.py`、`plugins/ndf/scripts/lib/design_results.py`、`plugins/ndf/scripts/supervise_lib/sprint.py`、`plugins/ndf/scripts/design-match.py`、`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/supervise_lib/engine.py`、`plugins/ndf/scripts/supervise_lib/commands.py`、`plugins/ndf/scripts/supervise_lib/steps.py`、`plugins/ndf/skills/design/references/design-template.md`、`plugins/ndf/scripts/tests/test_supervise_queue.py`、`plugins/ndf/scripts/tests/test_acceptance_checks.py`、`plugins/ndf/scripts/tests/test_spec_copy.py`、`plugins/ndf/scripts/tests/test_sprint_inputs.py`、`plugins/ndf/scripts/tests/test_design_results_rewrite.py`、`plugins/ndf/scripts/tests/test_design_match.py`、`plugins/ndf/scripts/tests/test_supervise.py` |
| #1142 | 実装する | — | （N3。設計承認ゲートの後に起票）`plugins/ndf/scripts/lib/parallel_liveness.py`、`plugins/ndf/scripts/lib/parallel_run.py`、`plugins/ndf/scripts/lib/parallel_observe.py`、`plugins/ndf/scripts/lib/parallel_end.py`、`plugins/ndf/scripts/lib/parallel_launch.py`、`plugins/ndf/scripts/lib/parallel_ledger.py`、`plugins/ndf/scripts/lib/parallel_lane.py`、`plugins/ndf/scripts/parallel.py`、`plugins/ndf/scripts/lib/parallel_args.py`、`plugins/ndf/scripts/relay_lib/hub_client.py`、`plugins/ndf/scripts/relay_lib/hub.py`、`plugins/ndf/skills/development-workflow/references/parallel.md`、`docs/specifications/ndf-parallel-organizer.md`、`plugins/ndf/scripts/tests/test_parallel_liveness.py`、`plugins/ndf/scripts/tests/test_parallel_stop_resume.py`、`plugins/ndf/scripts/tests/test_parallel_run.py`、`plugins/ndf/scripts/tests/test_parallel_end.py`、`plugins/ndf/scripts/tests/test_relay_hub.py` |
| #1142 | 実装する | — | （N4。設計承認ゲートの後に起票）`plugins/ndf/scripts/relay_lib/mark.py`、`plugins/ndf/scripts/relay_lib/record.py`、`plugins/ndf/scripts/lib/wait_notice.py`、`plugins/ndf/scripts/relay_lib/run.py`、`plugins/ndf/scripts/relay_lib/claude.py`、`plugins/ndf/scripts/relay_lib/hub.py`、`plugins/ndf/scripts/relay_lib/hub_client.py`、`plugins/ndf/scripts/relay_lib/install.py`、`plugins/ndf/skills/install-wrapper/SKILL.md`、`plugins/ndf/scripts/lib/claude_accounts.py`、`plugins/ndf/scripts/relay_lib/pin.py`、`plugins/ndf/scripts/relay_lib/switch.py`、`plugins/ndf/scripts/supervise_lib/claude.py`、`plugins/ndf/scripts/lib/deps.py`、`plugins/ndf/scripts/relay_lib/runtime.py`、`plugins/ndf/scripts/hook-env.py`、`plugins/ndf/scripts/hook_lib/token_guard.py`、`plugins/ndf/scripts/hook_lib/shell_checks.py`、`plugins/ndf/scripts/tests/test_relay_mark.py`、`plugins/ndf/scripts/tests/test_relay_tick.py`、`plugins/ndf/scripts/tests/test_relay.py`、`plugins/ndf/scripts/tests/test_relay_hub.py`、`plugins/ndf/scripts/tests/test_relay_account_use.py`、`plugins/ndf/scripts/tests/test_deps.py`、`plugins/ndf/scripts/tests/test_token_guard.py` |
| #1142 | 実装する | — | （N5。設計承認ゲートの後に起票）`plugins/ndf/scripts/lib/test_strategy.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/implement.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/proposals.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/propose.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor.py`、`plugins/ndf/skills/cross-refactoring/prompts/propose.md`、`plugins/ndf/skills/cross-refactoring/prompts/plan.md`、`plugins/ndf/skills/cross-refactoring/prompts/judge.md`、`plugins/ndf/skills/cross-refactoring/scripts/launch-cli.sh`、`plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh`、`plugins/ndf/scripts/lib/loop_drive.py`、`plugins/ndf/skills/cross-refactoring/scripts/drive.py`、`plugins/ndf/skills/cross-review/scripts/drive.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/baseline.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/template_check.py`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/final_fix.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope_check.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/gate.py`、`plugins/ndf/skills/cross-refactoring/prompts/final-fix.md`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/plan.py`、`plugins/ndf/skills/cross-refactoring/SKILL.md`、`plugins/ndf/skills/refactoring/references/code-smells.md`、`plugins/ndf/scripts/tests/test_test_strategy_collect.py`、`plugins/ndf/skills/cross-refactoring/tests/test_added_tests_collected.py`、`plugins/ndf/skills/cross-refactoring/tests/test_touched_symbols.py`、`plugins/ndf/skills/cross-refactoring/tests/test_scope_tests_by_imports.py`、`plugins/ndf/skills/cross-refactoring/tests/test_final_fix_outside.py`、`plugins/ndf/skills/cross-refactoring/tests/test_template_check.py`、`plugins/ndf/skills/cross-refactoring/tests/test_ci_only.py`、`plugins/ndf/scripts/tests/test_lib_loop_drive.py` |
| #1142 | 実装する | — | （N6。設計承認ゲートの後に起票）`plugins/ndf/skills/cross-review/scripts/review_lib/github.py`、`plugins/ndf/skills/cross-review/SKILL.md`、`plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py`、`plugins/ndf/scripts/lib/result_posts.py`、`plugins/ndf/skills/cross-review/scripts/drive.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/store.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/fix_result.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/commands/start_round.py`、`plugins/ndf/scripts/merged-steps.py`、`plugins/ndf/scripts/lib/run_state.py`、`plugins/ndf/skills/fix/scripts/fix-steps.py`、`plugins/ndf/skills/cross-review/tests/test_review_repo.py`、`plugins/ndf/skills/cross-review/tests/test_workspace_sync.py`、`plugins/ndf/scripts/tests/test_result_posts.py`、`plugins/ndf/skills/cross-review/tests/test_state_merge_fix.py`、`plugins/ndf/skills/cross-review/tests/test_state_start_round_sync.py` |
| #1142 | 実装する | — | （N7。設計承認ゲートの後に起票）`plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py`、`plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py`、`plugins/ndf/skills/backlog-refinement/scripts/upkeep.py`、`plugins/ndf/skills/backlog-refinement/scripts/upkeep_routes.py`、`plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py`、`plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_inputs.py`、`plugins/ndf/scripts/supervise_lib/sprint_close_steps.py`、`plugins/ndf/scripts/lib/sprint_loop_record.py`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/contracts/index.md`、`plugins/ndf/skills/backlog-refinement/SKILL.md`、`plugins/ndf/skills/backlog-refinement/tests/test_upkeep_rejected.py`、`plugins/ndf/skills/backlog-refinement/tests/test_upkeep_carry.py`、`plugins/ndf/skills/backlog-refinement/tests/test_upkeep_review_scoring.py`、`plugins/ndf/skills/backlog-refinement/tests/test_upkeep_rank.py`、`plugins/ndf/scripts/tests/test_sprint_close_depth_timeout.py` |
| #1142 | 実装する | — | （N8a。設計承認ゲートの後に起票）`plugins/ndf/scripts/glossary.py`、`.ndf/glossary.json`、`scripts/check-doc-line-limit.py`、`plugins/ndf/skills/requirements-design/references/glossary-format.md`、`plugins/ndf/scripts/plan-to-spec-steps.py`、`plugins/ndf/skills/plan-to-spec/SKILL.md`、`plugins/ndf/scripts/handoff.py`、`plugins/ndf/scripts/lib/handoff_doc.py`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/scripts/merged-steps.py`、`plugins/ndf/scripts/sprint-close.py`、`plugins/ndf/skills/retrospective/SKILL.md`、`plugins/ndf/skills/progress-tracking/SKILL.md`、`plugins/ndf/scripts/tests/test_glossary_split.py`、`plugins/ndf/scripts/tests/test_spec_finalize_many.py`、`plugins/ndf/scripts/tests/test_handoff.py`、`plugins/ndf/scripts/tests/test_merged_cleanup_stage.py`、`plugins/ndf/scripts/tests/test_sprint_close_verification_default.py` |
| #1142 | 実装する | — | （N8b。設計承認ゲートの後に起票）`plugins/ndf/scripts/lib/usage_capture.py`、`plugins/ndf/scripts/lib/usage_aggregate.py`、`plugins/ndf/scripts/lib/usage_ledger.py`、`plugins/ndf/scripts/lib/project_mvv.py`、`plugins/ndf/scripts/lib/project_mvv_signals.py`、`plugins/ndf/scripts/project-mvv.py`、`plugins/ndf/scripts/tests/test_usage_transcript_counts.py`、`plugins/ndf/scripts/tests/test_project_mvv_signals_repo.py` |
| #1142 | 実装する | — | （N9。設計承認ゲートの後に起票）`plugins/ndf/scripts/tests/test_external_ai_run.py`、`plugins/ndf/scripts/tests/test_relay.py`、`plugins/ndf/scripts/tests/cmd_match.py`、`plugins/ndf/scripts/tests/test_supervise_contract.py`、`plugins/ndf/scripts/tests/test_sprint_close_acceptance.py`、`plugins/ndf/scripts/tests/test_claude_account_env.py`、`plugins/ndf/scripts/tests/test_training_optout.py`、`plugins/ndf/scripts/tests/test_token_guard.py`、`plugins/ndf/scripts/tests/test_hook_deadline.py`、`plugins/ndf/skills/cross-review/tests/test_bg_wait.py`、`plugins/ndf/scripts/tests/test_auth_probe.py`、`plugins/ndf/scripts/tests/test_supervise_stall.py`、`plugins/ndf/scripts/tests/conftest.py`、`plugins/ndf/scripts/tests/test_sprint_loop.py`、`plugins/ndf/scripts/lib/worktree-testenv-sweep.sh`、`plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py`、`plugins/ndf/scripts/tests/waiting_templates.py`、`plugins/ndf/scripts/tests/test_waiting_templates.py`、`plugins/ndf/scripts/tests/test_progress_record_stages.py`、`scripts/tests/test_root_conftest.py`、`plugins/ndf/skills/cross-refactoring/tests/test_removed_review_phase.py`、`plugins/ndf/scripts/tests/test_no_command_parsing.py`、`plugins/ndf/scripts/tests/test_supervise_layout.py`、`plugins/ndf/scripts/tests/test_monitor_layout.py`、`plugins/ndf/skills/cross-review/tests/test_skill_layout.py`、`plugins/ndf/skills/release/tests/test_completion_check.py`、`plugins/ndf/skills/development-workflow/tests/test_conductor_entrypoints.py`、`plugins/ndf/scripts/tests/test_whole_test_timing.py` |
| #1142 | 実装する | — | （N10。設計承認ゲートの後に起票）`plugins/ndf/scripts/lib/monitor_patterns.py`、`plugins/ndf/scripts/lib/monitor_scan.py`、`plugins/ndf/scripts/lib/monitor_loop.py`、`plugins/ndf/scripts/lib/run_log.py`、`plugins/ndf/scripts/lib/run_core.py`、`plugins/ndf/scripts/lib/step_result.py`、`plugins/ndf/scripts/lib/secret_redact.py`、`plugins/ndf/scripts/lib/limits.py`、`plugins/ndf/scripts/lib/durable_keys.py`、`plugins/ndf/scripts/lib/durable.py`、`plugins/ndf/scripts/check-trigger.py`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/contracts/env/NDF_RUN_ID.json`、`plugins/ndf/contracts/files/`、`plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__check-trigger.py.json`、`scripts/contracts_lib/model.py`、`scripts/script-structure-allow/plugins__ndf__scripts__lib__run_log.py--fcntl--wrapped.json`、`conftest.py`、`plugins/ndf/skills/development-workflow/references/pace.md`、`plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh`、`plugins/ndf/scripts/hook.py`、`plugins/ndf/scripts/lib/launch-cli.sh`、`plugins/ndf/scripts/tests/test_run_log.py`、`plugins/ndf/scripts/tests/test_monitor_layout.py`、`plugins/ndf/scripts/tests/test_monitor_warnings.py`、`plugins/ndf/scripts/tests/test_check_trigger.py`、`plugins/ndf/skills/development-workflow/tests/test_workflow_guard.py`、`plugins/ndf/scripts/tests/test_lib_launch_cli_runtimes.py` |
| #1142 | 実装する | — | （N11。設計承認ゲートの後に起票）`plugins/ndf/scripts/supervise_lib/pr.py`、`plugins/ndf/scripts/supervise_lib/sprint_waves.py`、`plugins/ndf/scripts/supervise_lib/templates.py`、`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/supervise_lib/release_templates.py`、`plugins/ndf/scripts/behavior-check.py`、`plugins/ndf/scripts/design-match.py`、`plugins/ndf/scripts/check-trigger.py`、`docs/specifications/ndf-diff-kinds-and-docs-only-checks.md`、`plugins/ndf/scripts/tests/test_supervise.py`、`plugins/ndf/scripts/tests/test_design_match.py`、`plugins/ndf/scripts/tests/test_behavior_check.py` |
| #1142 | 実装する | — | （N12。設計承認ゲートの後に起票）`plugins/ndf/scripts/test-slot.py`、`plugins/ndf/scripts/test-run.py`、`plugins/ndf/scripts/hook.py`、`plugins/ndf/scripts/hook_lib/test_guard.py`、`plugins/ndf/scripts/lib/test_slots.py`、`plugins/ndf/scripts/lib/test_weight.py`、`plugins/ndf/scripts/lib/test_triage.py`、`plugins/ndf/scripts/lib/procs.py`、`plugins/ndf/scripts/lib/README.md`、`plugins/ndf/scripts/supervise_lib/steps.py`、`plugins/ndf/scripts/tests/test_test_slots.py`、`plugins/ndf/scripts/tests/test_test_guard.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/process.py`、`plugins/ndf/skills/development-workflow/references/parallel-work.md`、`conftest.py`、`docs/glossary/glossary.json`、`docs/glossary.md` |

## 着手の時点で決めること

worker が本文とコードを読み直したときに挙げた、子 Issue の着手の時点で決める直し方の選択である。どれも子 Issue の範囲（どの Issue を閉じるか）を変えない。

| 子 Issue | 決めること |
| --- | --- |
| `N1` | (1) 入力の 2 つの案（NEW-plan-refs-1 と NEW-run-refs）を 1 本にまとめた形で、触るファイルは 26 本になる。#2344 の参照の仕組みと #2664 のループの 1 回の扱いで分けると 2 本の PR にできる（#2344 の設計文書も参照の仕組みとエントリポイントの移しの 2 本に分けている）。分けるかは supervisor が決める。(2) #2344 の受け入れ条件 10（旧い形のプランを値のまま流す）は #1142 の前提（後方互換を意識しない）と食い違い、受け入れ条件 4（承認時の値との照合）は release_lib/approval.py・release-steps.py を触り RF8 と重なる。どちらも受け入れ条件の扱いの判断が要る。(3) #2344 の前提 2（柱 P6 のプランの型 lib/model/）は作業場所に無い（ls で確かめた）。この子は lib/model/ を作らず refs.py の型で持つ前提で書いた。(4) #2664 の前提 5（リリース承認ゲート の後の run --from は実行条件を評価しない）と #2344 の受け入れ条件 6（--from でも先に評価）は、キューの続きだけで評価する形でそろえた。(5) #2349 の 3 つ目と #2639 の 2 つ目は fits を false にした（行き先は SF2・SF11/SF13）。(6) #2638 は #2963 の設計文書の「開いている bug の割り当て」で RF7 の範囲外とされており、この子で受けても食い違わない。(7) #2528 の new_sprint_argv は lib/sprint_loop.py でなく lib/sprint_loop_argv.py にある（入力の file は移っている）。sprint_loop_argv.py はどの既存の子の触るファイルにも無い |
| `N2a` | (1) 3 つの主題（worker の受け渡し・closing keywordsのコミット・プランの書き換え）を 1 本にまとめているが、関数は 8 個で上限に当たる。prompts.py・worker_steps.py・templates.py を共有する #1734・#1742・#1958・#2005・#3129 で 1 本、#3111（supervise.py と新設の plan_edit.py）で 1 本に分けると PR の大きさと重なり（supervise.py は A1a・DC1・SF1b・SF2・SF5・SF13・K2・W1・RF5a が触る）が小さくなる。分けるかは supervisor が決める。(2) #1958 で手順の Skill（fix）を届けるには、対応表の rule_skills（規則の Skill だけを届ける決まり、#2376）を手順の Skill へ広げる判断が要る。代わりに指示文へ SKILL.md の絶対パスを書いて Read させる形（受け入れ条件 2 の別案）もある。(3) #1958 の受け入れ条件 1・3 は実測で、コードの変更だけでは満たせない。(4) #3129 で止めた後の直し方（worker に件名を直させ push し直す・機械で件名を `… （#N）` へ書き換える）は未決。既に push 済みのコミットの件名を直すと force push が要る。(5) pause の worker の会話を残すと使用量とディスクが増える（claude.py の minimal_args は --no-session-persistence を付ける）。(6) #2340・#2341 は #2335 の柱で、この子では閉じない。(7) 流れているプランの書き換えを断るかどうか（断るなら実行中の印を新設する）は未決 |
| `N2b` | (1) 4 つの主題（重なりの判定・受け入れ条件・設計の結果・マージの後の作業場所）を 1 本にまとめており、触るファイルは 27 本になる。主題ごとの 4 本に分けると、重なる子が admission.py は RF5a、engine.py は SF2・RF5a・W1、design_stage.py は SF1b・W1 にまとまる。分けるかは supervisor が決める。(2) #2539 の形は未決: 設計の結果の表で 1 課題に PR の単位が 2 つ以上あるとき、止めて人へ戻すか、単位ごとに実装のプランを組むか（後者は sprint.py の組み立てを変える）。PR の単位の名前をセルの先頭の括弧（#1142 の設計の結果の `（A0。#3196）` の形）で読む案は、今の parse_results が同じ課題の 2 行を食い違い（DesignResultsError）として止める振る舞いと合わせて決める。(3) #2596 で unread を止めるとき、設計 PR を持たない課題を --design に入れた場合（今のテスト test_no_pr_and_no_table_stay_unread の 1941-3 の形）も止めるかは未決。(4) #2357 の「退行しない条件」を機械でどう見分けるか（語の一致は分岐の多い判定になりやすい）は未決。条件の行に印を付ける決まりを requirements-design の手順に足す案がある。(5) #2553 の末尾の一致が 2 件以上のときの扱い（present・missing・unverified のどれにするか）は課題の本文でも未決。(6) #2274 の取り込まれた課題の設計文書は、#2453 の docs_of が番号でつながる文書として載せると読める（コードを読んだ推定。テストでの確認は未実施）。(7) #1811・#2056・#2453 は実装済みで、この子で閉じない |
| `N3` | (1) 入力は N3 を NEW-lane-liveness・NEW-parallel-state・NEW-parallel-lanes の 3 つに分けているが、NEW-parallel-lanes の #2936 と NEW-parallel-state の #2584・#2617・#2637・#2647 はコードが develop にあり（PR #2886・#2936 のマージ）、新しい変更で閉じるものが無い。新しい変更が要るのは #2649（#2477 を含む）・#2808・#3173・#3243 の 4 件で、1 子にまとめるか、生死の判定（#2649・#2477・#3173・#3243）と止めて再開（#2808）の 2 子に分けるかは supervisor が決める。(2) #2649 の実装は閉じた PR #2919 のブランチ sprint/lcore-13 にだけあり、develop から 974 コミット離れている。cherry-pick ではなく RF6a の判断の関数の形の上で作り直す前提で RF6a を先に要るものにした。(3) #2477 の残り（開発ループを持たないスプリントのレーンで一覧の時刻が進み続ける）は、時刻の更新元が未特定のまま（#2649 の前提 3）。#3173 の終わりの記録と #3243 の認証の止まりで塞がる範囲は広がるが、全部は塞がらない。(4) #3243 の「同じ会話へ /goal を入れ直す」は relay.py に会話へ文字を送る口が無いため、この案は relay.py restart（引継ぎの起動の 1 行で起こし直す）で代える。同じ会話を続けることが要るかは判断が要る。(5) #2808 の区切りの決め方（周の境かステップの境か）と既定を区切り待ちにすること（今の stop の振る舞いを変える）は公開インタフェースの変更で、設計承認ゲートで人が見る |
| `N4` | (1) 入力は N4 の中を NEW-relay-1（#1877・#2324）・NEW-hook-1（#1919）・NEW-relay（7 件）・NEW-deps-env（#3105）の 4 つに分けている。触るファイルは relay_lib/ が中心だが、#1919 は hook_lib/token_guard.py（SF12・DC5・DC7・W5・A3d の 5 子が触る）、#3105 は lib/deps.py（DC1・A3a・SF1b）と hook-env.py で別の主題である。1 子にすると触るファイルが 25 本になるため、分け方は supervisor が決める。(2) #2583 はコードが develop にあり（PR #2886 とコミット 4aaf8046a）、新しい変更で閉じない。#2563 も期待の 1 つ目は入っており、残りは同じ名前の止まったラッパーの起動のテストだけ。(3) #2461 は機能の追加で、#1142 の目的（AI が陥りやすい作りを無くす・定数からデータへ）の外にある。(4) #2438 の原因は未確認（課題の「疑わしい点」2 を採った）。first_written_at を足して比べる形は、課題の観測（シグナルファイルが Stop のたびに書き直される）からの推定で、再現のテストで確かめてから決める。(5) #3105 の「置き場の名前に python の版を含める」は、課題の案（別の置き場に作ってから入れ替える）と違う形を採った。入れ替えは走っているプロセスの sys.path が古い置き場のパスを指したまま消えるため、import が落ちる形が残る。どちらを採るかは設計で決める。(6) #1919 の未決（背景の sleep を全部拒むか、NDF のスクリプトと組んだものだけか）は課題の下書きどおり後者にした。(7) #2504 の効果（費用が減るか）は測っていない |
| `N5` | 判断が要る点: (1) #620 の収集の確かめ方（suite の宣言に collect_command を足す形を案にした。Issue は --collect-only・junit・計画の時点の 3 案を設計で決めるとする）。(2) #622 の受け入れ条件 3（refactoring の code-smells.md の「手を付ける範囲」を変えるか）は Issue に残る判断で、利用者が決める。検査のプランの 2 経路（refactor-scope.py・check-trigger.py scope）は今 CROSS_REFACTORING_SUSPENDED・refactor_suspended で cross-refactoring へ渡らない。案は cross-refactoring の側で差分から関数を引くため 2 経路のスクリプトを変えない。(3) #3038 で範囲外の新しいファイル（scope_check の NEW）を最終ゲートの直しで許すか。(4) #3038 の carte の事例で今の範囲の検査（judge_commits）が通った理由は未確認（target_scope が空か、呼び手の書き換えとして通ったかの記録を読んでいない）。(5) #3049 の受け入れ条件 3 の実測は CI テストの宣言の無いリポジトリで行う（ai-plugins は宣言があり、範囲テストを手元で走らせない）。(6) N5 は Issue の入力で NEW-crossrefactor-1 と NEW-xrefactor-scope の 2 本の案を 1 つにまとめた。触るファイルが targets.py・scope.py で重なるため 1 本にまとめるのが流しやすい。分けるなら #3038・#3049 を後の本にする |
| `N6` | (1) #3106 の lock はworktreeを外す側（merged-steps.py の remove_worktree・lib/run_state.py の片付け・cross-review の作り直し）すべてに unlock を足す必要がある。lock を外し忘れる経路が残ると、worktreeが外れずに残り続ける。外す経路を数え切ったかは実装の時点で確かめる（今見つけたのは merged-steps.py・lib/run_state.py・workspace.py の 3 か所）。cross-refactoring のworktree（prepare-worktrees.sh・refactor_lib/comparison.py・commands/setup.py の prune）も同じ形の穴を持つが、この子の範囲に入れるかは supervisor が決める。(2) #3106 の原因（どの prune が登録を消したか）は未確認のまま。lock は原因を特定しなくても prune から守れる。(3) #2770 の番号の規則は「元の PR 番号にそろえる」を採った。今の drive.py の path() が self.pr（元の番号）で結果ファイルを待ち、fix_prompt が current_pr を渡す食い違いを、渡す側を元の番号へ寄せる形で解く。今の番号へそろえる案もある。(4) #1707 の SKILL.md の起動の形は #1655 の設計（resolve.sh の入口）に合わせる前提で、#1655 の今の状態は確かめていない。(5) #2440 の取り込みに rebase を使うか merge を使うかは、PR のブランチの履歴の扱い（強制の push をしない）から rebase を採った |
| `N7` | 判断が要る点: (1) #1595 で本人の候補が無く依存先にしか無い番号を誤りにするか、依存先として退けたことを記録するか（Issue は決めて書くとする。案は誤りにする形）。(2) #1640 の置き場。メインディレクトリの .ndf/backlog/ は再起動と一時ディレクトリの掃除では消えないが、マシン・コンテナをまたがない（.ndf/.gitignore の backlog/ で追跡しない）。マシンをまたぐなら GitHub の側（マイルストーンの説明など）に置く設計が要る。(3) #2064 で --add の件数が上限を超えたときに上限を超えて残すか。(4) #2423 で費用の上限を効かせるか、usd を既定から外すか（Issue の 2 案。前者は supervise の work ステップへ費用の上限を渡す契約を足すことになり SF2・SF6 と重なる）。(5) #2423 の Issue は打ち切りを固定の 5400 秒とするが、今は limits.json の supervise.depth-work（5400 秒）から読む。(6) upkeep の状態の置き場の規則は lib/sprint_loop_record.py にも写しがあり、DC8（持ち主から引く）・RF7 が同じファイルを触る |
| `N8a・N8b` | 判断が要る点: (1) N8 は 12 件の Issue と 7 つの主題（用語集・確定仕様化・引継ぎ文書・後片付けと閉じる判定・使用量・帳簿の置き場・MVV の兆候）を持ち、1 本の子にすると触るファイルが 20 本を超え、先に要る子が 18 本になる。入力の案の名前どおり NEW-glossary-1（#1616）・NEW-records-1（#1715・#1893・#2293）・NEW-handoff-1（#1785・#1835）・NEW-usage-1 と NEW-usage-ledger（#1978・#1979・#2575・#2576。usage_capture.py・usage_aggregate.py が重なるため 1 本にまとめられる）・NEW-mvv-records（#2603・#2777）の 5〜6 本に分けるのが流しやすい。(2) #2316 は fits false（ロックの置き場が利用者の判断待ちで、Factory の側の実装も要る）。(3) #2614 は fits false（PR #2830 で実装済み。リリース後テストの後に閉じる）。(4) #1616 の受け入れ条件 4（1 つのコンテキストが 500 行を超えたとき、さらに分けるか除外するか）。(5) #2575 の既存の行（プラグインのデータの置き場の 993 行）を移すか。#1142 の前提（後方互換を意識しない）なら移さず、置き場を変えた日を集計の注記に出す形がある。(6) #2576 の直し方（停止の後に読み直す・次の停止で差を書く・集計で会話の記録から補う）の選択。hook の上限は 10 秒で、読み直しの待ちは入れにくい。集計で補う形は会話の記録が消えた期間に補えない。(7) #2603 を書く側で直すと、既にある worktree の repo の行（devbasex/ai-plugins で unknown 43 行）は数えないままになる。#1142 の前提に沿えば読む側の変換は置かない。(8) #1979 の設計は閉じた PR #2577 の design/issue-1979 のブランチにあり、決定 1〜11 とテスト設計を材料に使える |
| `N9` | (1) 入力の 5 つの案（NEW-tests-1・NEW-test-slots・NEW-test-isolation・NEW-testenv-reclaim・NEW-test-trim）を 1 本にまとめた形で、#2711 を除いても触るファイルは 28 本になる。(2) #2711 は承認済みの設計（PR 2744。閉じたがブランチ design/issue-2711 に文書が残る）を持つ新しい仕組みで、fits を false にして独立の子を勧めた。独立させるときの触るファイルは設計文書の「設計の結果」の 17 本、先に要るものは DC3（hook.py・lib/procs.py）・SF2・SF6（supervise_lib/steps.py）・SF1b（test-run.py）・W4（refactor_lib/process.py）・W5（hook.py）になる。(3) #1735 は原因が未特定で、製品のコードの直しになりうる。(4) #2974 の C（500 行の確かめ）は RF2（行数の上限を 1,000 の安全網にする）と同じ主題で、RF2 の後に消す。(5) #2798 の入力の file（lib/container_reach.py）は呼び出し口で、判定は lib/worktree-testenv-sweep.sh の _reclaim_location_seen にある。テストは plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py（DC5 の触るファイルの test_testenv_reclaim.py と同じもの）。(6) prereq はファイルの重なりから: test_relay.py・test_testenv_reclaim.py（DC5）、test_supervise_slow.py（SF6。#2758 の 1 件は既に直っている）、test_sprint_loop.py（SF11・RF7）、test_token_guard.py（SF12）、worktree-testenv-sweep.sh（W5）。(7) #1967 の原因はコードを読んだ推定（壁時計 2.5 秒の確かめ）で、並列でだけ落ちることの再現は未実行 |
| `N10` | (1) この子は 4 つの主題（監視の照合と警告・実行の記録・マージの hook・kiro の起動）の寄せ集めで、触るファイルは 28 本になる。#2338 は設計どおり Pull Request 2 本の規模のため、#2338・#2059 で 1 子、#1411 を W3 の前の小さな子、#2446 と #2448 をそれぞれ別の子にする分け方もある。決めるのは supervisor。(2) #1411 の直し方は課題の「修正レイヤー」の 2 つのうち 2（一般語を CLI の出す形に限る）を採った。1（codex がプロンプトを写した範囲を照合から外す）は、err.log のプロンプトの写しの始まりと終わりの印を実物で確かめていない（codex exec を起動していない）。(3) #2448 の kiro-cli が .amazonq/cli-agents を書き換える条件は未確認。kiro-cli 3.3.0 の agent list を一時ディレクトリで打った結果、.amazonq/cli-agents/x.json は書き換わらず .bak も出なかった。書き換えは chat の起動の側で起きるとみられるが、chat は打っていない。起動の前後で戻す形は、条件が分からなくてもworktreeを元に戻せる。(4) #2446 の本番チャネルの判定の部品 lib/delivery.py の load_delivery(root) は手元のディレクトリの宣言しか読まない（delivery.py 241 行目）。別のリポジトリの宣言を GitHub から読む読み手は新しく要る。(5) #2338 の実行の核の終わりの行の cause は SF1a の causes.json の種類を載せる形にそろえると、SF1b と重ならない |
| `N11` | (1) 入力は「SF2 から分けた」子だが、触るファイルの 5 本（templates.py・pr.py・paths.py・release_templates.py・design_steps.py の欄）はすべて SF2 の触るファイルにあり、SF2 の後に流す。RF5a は supervise_lib/ の残り（templates ほか）の兆候を受け持つため、templates.py を触る順で RF5a の後にもなる。(2) #3023 の原因は雛形でなく test-run.py の cmd_whole の順序（テスト環境を起こす container_reach.env_for が文書だけの判定より前）と読んだ。未実行の推定のため fits を false にし、行き先の判断を supervisor に残す。(3) #3108 の入力の file（delivery_templates.py の verify の前の git pull）は作業場所の grep で見つからず、git pull は release_templates.py の 329 行（開発版と本番の verify）にある。#2140 と同じ 1 か所で直る。(4) #3003 の入力の file（verify_steps.py）は移っており、振る舞いの確認のステップは supervise_lib/paths.py の BEHAVIOR_STEPS にある。(5) #2370 の「未解決 0 件」の種類は cross-review の drive.py が返す結果（unresolved・final）から作る。drive.py は RF1b・SF1b が触るため、種類を返す側は SF1b、行き先を選ぶ側をこの子にした。(6) #3112 は CI テストの宣言の無いリポジトリの経路だけが残る（本文のとおり）。hook で断る案は #2711 の重い処理の案内（hook_lib/test_guard.py）と重なるため、この子では指示の規則だけにした |
| `N12` | 設計 PR #2744 が閉じた理由と、設計文書の決定をそのまま使うか（ブランチ `design/issue-2711` の設計文書は承認ゲートを通っていない） |

## 人へ残す判断

無し

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 実装が develop にある 11 件の再現 | 決定 72 の 11 件は、コードを読んで直しが develop にあると見た。再現の手順を通したのは一部で、残りは受け持つ子 Issue の PR で通す |
| #2649 の実装 | 閉じた PR #2919（ブランチ `sprint/lcore-13`）にだけあり、develop から 974 コミット離れている。N3 は RF6a の後に、develop の上で作り直す |
| #2448 の kiro の書き換え | kiro-cli 3.3.0 の `agent list` では `.amazonq/cli-agents` が書き換わらなかった。`chat` で書き換わるかは確かめていない。N10 の着手の時点で再現を確かめる |
| 触るファイルの数 | 子ごとの触るファイルは 2026-11-13 の develop で数えた。着手の時点でほかの子 Issue のマージで変わる |
