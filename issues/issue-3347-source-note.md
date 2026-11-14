# #1142 の作り直しの根拠の原文: オーガナイザーから lrebuild への申し送り（2026-11-13〜14）

**この文書は、git の外にある引継ぎ文書 `.ndf/handoff/lrebuild-note-rebuild-1142-label.md`（メインディレクトリ。`.ndf/.gitignore` で除外）の写しである。** #3347 の決定 104 で、設計と Issue が根拠として指す原文をリポジトリから辿れるようにするために置いた。写した時点は 2026-11-14 の 11:5x JST で、以後の追記は写さない（git の外の原文はレーンの作業で書き換わり、消えることもある）。

- 設計 [issue-1142-design-next.md](issue-1142-design-next.md) の「あるべき姿の根拠」の 1 行目（利用者の決定 2026-11-13 23:1x JST）は、下の節「作り直しの置き場の決め直し」を指す
- #3303（S1）と #3290（AD3）の本文が指す「ndf-next を試す起動の手順」は、下の同じ名前の節を指す（Issue の本文の向け直しは設計承認の後。#3347 の詳細設計の「承認の後に直す Issue の本文」）
- 本文は原文のまま写し、見出しの階層だけを 1 つ下げた。原文の中の `.ndf/handoff/` のパスは、原文の置き場（git の外）を指す

---

## オーガナイザーから lrebuild へ: #1142 の受け入れ条件の追加（2026-11-13 20:3x JST）

利用者の指示: 「#1142 で改善する可能性がある Issue に専用のタグを付ける。#1142 の受け入れ条件は、このタグの Issue が #1142 の改善によってすべて閉じること」

- ラベル `rebuild-1142` を 283 件に付けた: https://github.com/devbasex/ai-plugins/issues?q=label%3Arebuild-1142
  - 開いている子 Issue 56 本
  - 開いている Issue 375 件を分類して「作り直しで閉じられる」とした 227 件（分類の結果と理由: `.ndf/handoff/rebuild-1142-classification.json`。`verdict` が yes のもの）
- #1142 の本文の「受け入れ条件」の先頭に、この条件を書き足した
- **詳細設計 `issues/issue-1142-design-detail.md` の「開いている bug の割り当て」で「範囲外」とした 106 件のうち 92 件にラベルが付いた。** 実装計画（子 Issue の範囲・流す順番）を、ラベルの Issue をすべて閉じる形へ組み直してほしい。組み直しは設計の変更なので、設計 PR で出して設計承認ゲートを通す
- ラベルの Issue のうち、作り直しで閉じられないと判断したものがあれば、外す理由を添えてオーガナイザーへ依頼として送る（ラベルを外すのは利用者の同意の後）

### 追記（2026-11-13 20:4x JST 利用者）

- **lrebuild は #1142 の関連レーンを同時に 6 本まで使ってよい。** 他のリポジトリの開発はすべて止めるので、#1142 に全力で注力する
- レーンの案（レーン名・子 Issue・触るファイルが重ならない組・after）を、実装計画の組み直しが済んだ時点でオーガナイザーへ送る。オーガナイザーが `parallel.py lane p6 add` で足す。上限は `relay.py cap --named 13 --plans 13` に上げてある
- 足すまでの間も、設計 PR（ラベル 283 件を閉じる形への組み直し）は進める

### 設計承認ゲートの答え（2026-11-13 21:4x JST 利用者）

- PR #3250 の設計: **承認（推奨どおり）**。推奨の (1)〜(6) をすべてそのまま採る
- (1) の 6 件（#1571 #1601 #2316 #2341 #3113 #3189）のラベル `rebuild-1142` はオーガナイザーが外した
- Lane 6 本も承認。上限は `relay.py cap --named 13 --plans 18` に上げた（6 本 × Plan 2 本。検査は終わった Lane も数えるため）
- **Lane の定義はオーガナイザーが `parallel.py lane p6 add` で足す。** lrebuild は子 Issue を起票した後、足す 5 本（lr-flow・lr-data・lr-rf・lr-fix-a・lr-fix-b）の定義を `.ndf/parallel/p6/work/lanes/<名前>.json` に 1 本ずつ書く。形は plan.json の Lane の 1 要素（name・input{kind:sprint, issues:[子 Issue の番号]}・plans:2・touches・after:["lflow","lbr2","lsw"]・handoff）。lrebuild 自身の書き換え（plans 2・issues）は `lrebuild.json` に書く（set で当てる）。書いたらオーガナイザーへ依頼で知らせる

### 作り直しの置き場の決め直し（2026-11-13 23:1x JST 利用者）

lflow の PR 3247（#3092 の実装、246 ファイル・major 指摘 25 件・CI 失敗 70 件）を受けて、利用者が「別名のリポジトリを新しく作って 0 からやった方が早くない？」と問い、オーガナイザーの比較の後に **「同じリポジトリ、別のディレクトリ」** を選んだ。

- **#1142 と #3092 は、同じリポジトリの別のディレクトリ（例: `plugins/ndf-next/`）に 0 から作り、できたら `ndf` と入れ替える。** 今の NDF（`plugins/ndf/`）は壊さず、開発の道具として使い続ける。今のコードは移すものではなく参考として読む
- CI・CD・ruleset・`.ndf/` の宣言・Issue（ラベル `rebuild-1142`）はこのリポジトリのものをそのまま使う。受け入れ条件「ラベルの Issue がすべて閉じる」は変えない（入れ替えの時点で閉じる形でよい）
- 承認済みの設計（#1142 の設計・詳細設計、PR #3191、PR #3250）は新しい実装の材料として使う
- **lrebuild がすること:** 新しいディレクトリでの作り方の設計（ディレクトリの名前・配布と入れ替えの手順・子 Issue と Lane の組み直し・#3092 #3097 #3098 の取り込み）を設計 PR にして、設計承認ゲートへ出す
- 新しいディレクトリは今の NDF のファイルと重ならないため、lflow・lbr2・lsw を待つ必要は無い（after から外してよい）。#1142 の Lane は同時 6 本まで（lrebuild を含む）
- 足した 5 本（lr-*）は、新しい計画ができるまで起動しないよう after に lrebuild を足して止めてある。新しい計画ができたら、Lane の定義を `.ndf/parallel/p6/work/lanes/` に書いて知らせる（オーガナイザーが set で当てる）
- lflow は PR 3247 を閉じて Lane を終える（`.ndf/handoff/lflow-gate1-answer-3247.md`）

### Lane の定義を当てた（2026-11-14 05:2x JST オーガナイザー）

- 6 本とも `lane set` で当てた。**ただし lr-* の 5 本は `after: ["lrebuild"]` のまま止めてある**（after が空だと当てた瞬間に起動し、X0 の土台が無いまま着手するため。各 Lane の引継ぎ文書にも X0 を待つ指示が無い）
- **X0（#3282）を develop へマージしたら、オーガナイザーへ依頼（`parallel.py request-ask`）で知らせる。** オーガナイザーが 5 本の after を空にして起動させる。そのときまでに、各 Lane の引継ぎ文書（`.ndf/handoff/sprint-lr-*.md`）に担当の子 Issue と最初にすることを書いておく（今は lane add 時の古い中身のまま）
- lr-rf の #3092・#3097・#3098 が、終わった lflow の課題と重なり検査（I19）に落ちたため、lflow の課題を #3144 だけに置き換えた
- lrebuild の課題（#3282 #3283 #3303 #3304）は当てたが、今動いている会話は起こし直していない（`set` は自動で起こし直さない）。今の会話のまま X0 から進めてよい

### ndf-next を試す起動の手順（2026-11-14 06:2x JST、利用者の指摘とオーガナイザーの実測）

利用者の指摘: 「ndf-next のテストには ndf-next だけがプラグインとして入った claude を起動する必要がある。ndf が入ったままだと同じ Skill があり、AI が誤った方を使う可能性が高い」

オーガナイザーの実測（claude 2.1.325、`--output-format stream-json` の init の plugins と skills）:

| 起動 | 読み込まれたプラグイン | `ndf:` の Skill |
| --- | --- | --- |
| `claude --plugin-dir plugins/ndf-next`（設計の決定 74・S1 の手順） | ndf-next と **ndf の両方**（ndf の hook も走る） | 45 |
| `claude --settings '{"enabledPlugins":{"ndf@ai-plugins":false}}' --plugin-dir plugins/ndf-next` | ndf-next だけ | 0 |

lrebuild に頼むこと（今の子 Issue の受け入れ条件へ入れる。X1 か S1 の、早く入る方）:

1. ndf-next を試す起動をスクリプトで固定する（例 `plugins/ndf-next/bin/` に置く）。入っている ndf を `--settings` の `enabledPlugins` で外し、`--plugin-dir` で ndf-next を読む。利用者の設定ファイルは書き換えない。手順書へ手で 1 行書くだけにしない
2. ndf-next が自分で起こす `claude -p`（worker・レビュー・振る舞いの確認）も同じ形で起こす。子のプロセスが古い ndf で動かないようにする
3. 起動の init の plugins に ndf が無く ndf-next があることを確かめるテスト（か検査）を置く
4. 設計の決定 74 と S1 の手順を、このスクリプトを使う形に直す（次の設計 PR か、該当の子 Issue の PR で）

### 追記: 最小構成の claude -p は二重にならない（2026-11-14 06:3x JST 実測）

- 今の `supervise_lib/claude.py` の `minimal_args`（`--setting-sources ""`・`--strict-mcp-config`・`--disable-slash-commands`・`--no-session-persistence`・`--system-prompt`）で起こすと、利用者の設定を読まないため ndf は読み込まれない（init の plugins に ndf が無く、Skill 0・スラッシュコマンド 0・MCP 0）。`--plugin-dir plugins/ndf-next` を足すと ndf-next だけが読まれる
- **二重になるのは `full`（Skill を回すステップ。設定とプラグインをそのまま読む）の起動だけ。** 上の「頼むこと」2 は `full` に当たる起動が対象。最小構成の形は ndf-next でもそのまま使ってよい

### CI の負荷を減らす（2026-11-14 06:4x JST、利用者の依頼「CI のスピードと負荷を検討」とオーガナイザーの実測）

- 実測（2026-11-12 以降の実行 1,000 件）: 1 回の push で走るジョブは 25 本。Python tests の所要は中央値 13.4 分（2 分割）。開始待ちは中央値 0 分・最大 3.1 分。Free プランなので同時に走るジョブは組織全体で 20 本まで
- **ndf-next の Lane の PR（#3318・#3319・#3320 など。差分は `plugins/ndf-next/` だけ）でも、古い ndf の pytest（2 分割 × 13 分）・Runtime plugin validation（10 本）・Runtime plugin smoke（3 本）が走っている。** ndf-next は自分の workflow（`ndf-next-tests` 16 秒）で確かめられる
- 頼むこと（X1 #3283 の PR #3321 が `.github/workflows/` を触っているので、そこか、すぐ後の小さな PR で。その場で直してよい 4 条件に収まる）:
  1. `scripts/ci-heavy-skip.py` に「差分が `plugins/ndf-next/` の下だけ（と、`docs` の宣言で文書とみなすファイル）なら重いジョブを飛ばす」を足す。pytest・Runtime plugin validation・Runtime plugin smoke が、今の「文書だけ」と同じ形で結果を返して飛ぶこと（必須のチェックが通る形を保つ）
  2. 足した後に、ndf-next だけの PR で 3 つの重いジョブが飛び、`ndf-next-tests` だけが走ることを 1 回確かめる
- 古い ndf の pytest の分割を 4 に増やすかは、1 の効果を見てからオーガナイザーが決める（今は頼まない）

### 設計レビューの指摘と依存ライブラリの方針（2026-11-14 08:3x JST、オーガナイザーから）

- #1142 の設計レビューの指摘を 7 本の Issue にした: A 入れ替え #3341・B 機能の網羅 #3342・C 並行する変更 #3343・D DBOS の置き方 #3344・E hook の基盤 #3345・F 一時的な失敗 #3346・G 順番と依存の版 #3347（ラベル `rebuild-1142`。一覧は #1142 の本文の「設計レビュー（2026-11-14）」）
- **D #3344 が決まるまで、lr-flow は C2 #3291 に着手しない**（利用者の決定。08:3x に lr-flow へ伝えた）
- **依存ライブラリの方針（利用者の指示）:** ndf-next の依存はできるだけ最新の安定版を使う。安定版の概念が無いもの・安定版が古すぎるもの（例: httpx の安定版 0.28.1 は 2024-12）は最新を使う。今遅れている 13 本の表は #3347
- #3336・#3337（利用上限でのアカウントの切り替え）にラベル `rebuild-1142` を付けた。行き先は M3 #3297（#3346）
