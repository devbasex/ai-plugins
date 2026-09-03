# 起票先を決める

**「起票する」を選んだときだけ読む。** 範囲内へ入れる場合と起票しない場合には、起票先が
要らない。

NDF を使う開発では、関わるリポジトリが 2 つになることがある。**どちらへ起票するかは、
課題の性質が決める。** 見つけた場所は決め手にならない。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 配布元のリポジトリ | NDF の Skill・エージェント・hook の実体を持つリポジトリ |
| 開発対象のリポジトリ | NDF を使って開発している側のリポジトリ。`gh repo view` が返すもの |
| 起票先 | `gh issue create` が issue を作るリポジトリ |

**1 つのリポジトリで開発していて、配布元を別に持たない場合はこの判断が要らない。**
そのままそのリポジトリへ起票する。

## 判断表

| 課題の性質 | 起票先 |
| --- | --- |
| 工程と Skill の手順そのもの | 配布元のリポジトリ |
| 開発対象のコード・データ・設定・運用 | 開発対象のリポジトリ |
| 両方にまたがる | 配布元と開発対象の 2 つのリポジトリ |

**どちらで直せるかで分ける。** 手順書の記述が原因なら、開発対象のリポジトリを直しても
次の利用者が同じ場所で止まる。逆に、開発対象の設定の誤りを配布元へ起票しても、配布元には
直す対象が無い。

## 起票先のリポジトリを決める

配布元のリポジトリの名前を、3 段で解決する。**上から順に見て、決まった時点で止める。**

| 段 | 何を見るか | 決まらないとき |
| --- | --- | --- |
| 1 | 環境変数 `NDF_SKILL_REPO`（`<所有者>/<リポジトリ>` の形） | 段 2 へ |
| 2 | `plugins/ndf/` を持つ取得元の clone の `remote.origin.url`。1 つに絞れたときだけ採る | 段 3 へ |
| 3 | 利用者に聞く | 推測で `--repo` を渡さず止まる |

段 2 の位置はランタイムで違う。

| ランタイム | 取得元の clone の位置 |
| --- | --- |
| Claude Code | `~/.claude/plugins/marketplaces/<取得元>` |
| Codex | `~/.codex/.tmp/marketplaces/<取得元>` |
| Kiro / agy | clone した作業ディレクトリ |

**取得元は 1 つとは限らない。** この位置には登録したすべての取得元の clone が並ぶ。

```console
$ ls -d ~/.claude/plugins/marketplaces/*/
/home/ubuntu/.claude/plugins/marketplaces/ai-plugins/
/home/ubuntu/.claude/plugins/marketplaces/anthropic-agent-skills/
/home/ubuntu/.claude/plugins/marketplaces/claude-plugins-official/
```

**先頭から見て最初の GitHub の取得元を採ると、配布元ではないリポジトリが決まる。** 上の例で
`anthropic-agent-skills` は `https://github.com/anthropics/skills.git` を指しており、条件を
GitHub の取得元であることだけに置くと候補になる。並びは名前順であって、配布元が先に来る
保証は無い。

**NDF の実体を持つ clone だけを候補にする。** `plugins/ndf/` があることが、その clone が
NDF の配布元であることの直接の証拠になる。取得元の名前や `marketplace.json` の `name` は、
fork や登録名の変更で変わるうえ、同じ名前を別の取得元が名乗れる。

**取得元を持たないランタイムでは、現在地の clone が候補になる。** Kiro と agy は clone した
作業ディレクトリから導入するため、決まった置き場所が無い。**現在地は clone の根とは限らない**
ため、`git rev-parse --show-toplevel` で根へ戻してから見る。

**現在地にも同じ絞り込みを掛ける。** いま開いているリポジトリが配布元とは限らない。無条件に
採ると、開発対象のリポジトリが配布元として決まる。

```bash
SKILL_REPO="${NDF_SKILL_REPO:-}"                                  # 段 1

if [ -z "$SKILL_REPO" ]; then                                     # 段 2
  found=""
  candidates=(~/.claude/plugins/marketplaces/*/ ~/.codex/.tmp/marketplaces/*/)
  here="$(git rev-parse --show-toplevel 2>/dev/null || true)"     # Kiro / agy は現在地の clone
  if [ -n "$here" ]; then candidates+=("$here"); fi

  for clone in "${candidates[@]}"; do
    [ -d "$clone/plugins/ndf" ] || continue                       # NDF の実体を持つ clone だけを見る
    url="$(git -C "$clone" config --get remote.origin.url 2>/dev/null || true)"
    case "$url" in
      *github.com[:/]*) found="$found${url%.git}
" ;;
    esac
  done
  found="$(printf '%s' "$found" | sed 's#.*github.com[:/]##' | sort -u)"
  if [ "$(printf '%s' "$found" | grep -c .)" = 1 ]; then          # 1 つに絞れたときだけ採る
    SKILL_REPO="$found"
  fi
fi
```

**絞っても複数残るときは段 3 へ倒す。** fork と本家を両方登録した利用者では、どちらも
`plugins/ndf/` を持つ。同じ名前が 2 つの取得元から出たときも、`sort -u` が 1 つにまとめる。

**版ごとの配置からは読めない。** `~/.claude/plugins/cache/<取得元>/<名前>/<版>/` には `.git`
が無く、git の作業ツリーではない。

**段 3 に達したら止まる。** 推測で `--repo` を渡すと、別のリポジトリへ issue が作られる。
作った側からは成功したように見えるため、読む人のいない場所に残ったことに気づけない。

段 1 と段 2 で決まった場合も、起票の前の提示に起票先を含める。取得元を分けた利用者や
fork した利用者では、解決した名前が実際の配布元と違うことがある。

## 重複の確認と起票

**決めた起票先に対して行う。** `--repo` を省くと、いま作業しているリポジトリへ向かう。

```bash
ISSUE_REPO="$SKILL_REPO"                                          # 判断表で決めたほう

gh issue list --repo "$ISSUE_REPO" --state open --search "<課題を表す語 2〜3 個>"
gh issue create --repo "$ISSUE_REPO" --title "<何が起きるか>" --body-file <本文のファイル>
```

閉じるときも同じ形で渡す。

```bash
gh issue close <番号> --repo "$ISSUE_REPO"
```

`<所有者>/<リポジトリ>#<番号>` の表記は本文の中で相手を指すためのもので、`gh issue close`
の引数としては受け付けられない。

## 両方にまたがる課題

**配布元を先に起票する。** 番号が先に決まれば、開発対象の側の本文へその番号を書ける。
逆順にすると、どちらの本文にも相手の番号が無い状態が一度できる。

1. 配布元へ起票する
2. 開発対象へ起票し、本文の「由来」へ配布元の番号を `<所有者>/<リポジトリ>#<番号>` の形で書く
3. 配布元の issue へ、開発対象の番号をコメントで足す

```bash
gh issue comment <配布元の番号> --repo "$SKILL_REPO" \
  --body "開発対象の側は <所有者>/<リポジトリ>#<番号> として残した。"
```

**2 件を同時に作って後から両方を編集しない。** 編集を忘れると、相互の参照が片方だけ残る。
