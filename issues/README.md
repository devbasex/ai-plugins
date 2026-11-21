# issues/ の構成

作業中の調査・実装計画・依頼文を置く。**1 つの issue につき 1 ファイル**とし、
完了したものは確定仕様へ移してから消す（下の「完了した課題の扱い」）。

| 場所 | 中身 |
| --- | --- |
| `issues/*.md` | 未完了の issue の実装計画・研究メモ。1 issue = 1 ファイル |
| `issues/old/` | 11.0.x までに完了した issue の計画・実機試行の記録・報告書・調査資料。新しく足さない |

`old/` の中身は履歴として読むためのもので、運用判断の根拠には使わない。確定した仕様は
[docs/specifications/](../docs/specifications/) を、リリース済みの変更点は [CHANGELOG.md](../CHANGELOG.md) を参照する。

## 完了した課題の扱い

**完了した課題の文書は、確定仕様（[docs/specifications/](../docs/specifications/)）へ移した後に
`ndf records spec finalize` で消す。** 文書を消すと、それを `pending_source` に持っていた用語集の語の
出所が確定仕様へ移る。消す前の版は git の履歴に残るので、残る文書から指すときは消す前の
コミットの 40 桁の SHA に固定したリンク（`https://github.com/devbasex/ai-plugins/blob/<SHA>/issues/<ファイル>`）にする。

```bash
ndf records spec finalize --spec docs/specifications/<確定仕様>.md --remove issues/<消す文書>.md
```

`--spec` と `--remove` は 1 つのパスに 1 つずつ繰り返す。`finalize` は消す文書を未コミットの編集ごと
作業ツリーから消し、用語集の正本（`docs/glossary/glossary.json`）と生成 Markdown（`docs/glossary.md` と
`docs/glossary/` の下）を作り直して上書きする。消した文書を git の履歴から戻せるのは、`HEAD` までにコミットした版だけである。
打つ前に次の 2 つを確かめる。どちらかが通らなければ、消す文書と用語集の変更をコミットしてから、2 つとも確かめ直す。
2 で `!!` の行（`.gitignore`・`.git/info/exclude`・グローバルの除外設定に一致する無視されたファイル）が
出たら、コミットでは残せないため、作業ツリーの外へ退避してから確かめ直す。2 の対象は `finalize` が消す・
上書きするパスだけなので、`docs/glossary/` の下の `.DS_Store` やエディターの `.swp` は出ず、退避しなくてよい。

```bash
# 1. 消す文書ごとに、HEAD に版があること（終了コードが 0。ステージしただけ・未追跡なら 0 以外）
git cat-file -e "HEAD:issues/<消す文書>.md"
# 2. 消す文書と用語集の書き換え先（SSOT と生成 Markdown）に、未コミット・未追跡・無視された変更が無いこと（出力が空であること）
git status --short --untracked-files=all --ignored=matching -- issues/<消す文書>.md docs/glossary.md docs/glossary/glossary.json 'docs/glossary/*.md'
```

**`old/` は 11.0.x までに移したものの保管で、新しく足さない。** 下の節は、そこに残る文書の参照を
直すときのためにある。

## old/ へ移すときの参照

**`old/` へ移すと、相対パスで書いた参照が壊れる。** 移した文書から見た深さが 1 段変わり、
移していない文書から移した文書への参照も行き先を失う。`issues/` は `docs/` と同じくリンクの
検査（`scripts/check-markdown-links.py`）の走査に入っているため、移した Pull Request の検査が
`missing link target` で落ちる。

**直すときは `](` と `)` の間だけを変える。** 角括弧の中の文言と行の他の部分は、当時の記述の
まま残す。記録が何を指していたかは変わらず、読み手を運ぶ道筋だけが直る。

| 壊れ方 | 直し方の例 |
| --- | --- |
| 移した文書から `docs/` を指す | `../docs/x.md` を `../../docs/x.md` にする |
| 移した文書から、一緒に移した文書を指す | `old/y.md` を `y.md` にする |
| 移していない文書から、移した文書を指す | `y.md` を `old/y.md` にする |

## いま開いている課題を見る

**課題の一覧はここに置かない。** 開閉は GitHub 側で起き、書き写した一覧はその事実を
受け取らないため、書いた時点から古くなる。

```bash
gh issue list --limit 100                     # 開いている課題
gh issue list --milestone "<まとまりの名前>"   # まとまりごと
```

**このディレクトリにあるのは、着手した課題の計画と調査だけである。**

```bash
ls issues/*.md | grep -v '^issues/README\.md$'   # 進行中のもの
ls issues/old/                                   # 完了したもの
```

着手するときに `/ndf:implementation-plan` で `issues/issue-<番号>-<内容>.md` を 1 ファイル
作る。着手していない課題のファイルは作らない。

並行して進める複数の課題は、**進行側が 1 つの実行計画にまとめる**。置き場所は
`issues/execution-plan-<キー>.md`（キーはマイルストーンの先頭の 2 桁の連番、または
`issue-<最小の課題番号>`）で、束ごとの依存・触るファイルと節・起動してよい本数を持つ。

**実行計画はコミットしない。** 見直しのたびに Pull Request を通すと、計画の更新がマージを待つ。
すべてマージされたら、測定を書き足した全文を最後にマージした Pull Request へコメントとして投稿し、
ファイルを消す。形と手順は `issue-plan-strategy` の `references/execution-plan.md` が持つ。

`old/parallel-batch-<連番>/` は 2026-09-02 までの進め方の記録である。全体指示書を主ディレクトリへ
コミットする運用は `develop` への直接のコミットの禁止と両立しないため、実行計画へ置き換えた。

## ファイル名の付け方

`issue-<issue 番号>-<内容を表す英小文字とハイフン>.md` とする。issue 番号を持たない調査資料は
番号を付けず、内容だけで名前を付ける。1 ファイルに収まらない規模のときだけ
`issue-<番号>-<内容>/` のディレクトリにして、`01-` から始まる連番のファイルへ分ける。
