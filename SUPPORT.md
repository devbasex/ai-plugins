# サポート

質問・不具合の報告・要望は、すべて **issue** で受け付けています。Discussions は有効にして
いません。窓口を 1 つにして、過去のやり取りを検索できる場所へまとめています。

## 先に読む場所

尋ねる前に、次の順で探すと早く解決することがあります。

| 探す内容 | 場所 |
| --- | --- |
| 導入の手順、ランタイムごとの違い | [README.md](./README.md) の「利用方法」 |
| プラグインの中身、Skill の一覧 | [plugins/ndf/README.md](./plugins/ndf/README.md) |
| 開発と Pull Request の進め方 | [CONTRIBUTING.md](./CONTRIBUTING.md) |
| 過去に同じ事象が報告されていないか | [issue の一覧](https://github.com/devbasex/ai-plugins/issues?q=is%3Aissue) |

**閉じた issue も検索してください。** 修正済みの事象は閉じた側にあります。

## issue を分ける

| 種類 | 内容 | 書くこと |
| --- | --- | --- |
| 不具合の報告 | 書かれたとおりに実行して、書かれたとおりに動かない | ランタイムと版、OS、再現手順、期待した結果、実際の結果 |
| 使い方の質問 | どう使うかが分からない、意図した動きかどうかを確かめたい | やりたいこと、試したこと、その結果 |
| 要望 | 足りない機能、改善の提案 | 解決したい状況、案、代替案 |

**再現手順と、実行したランタイムの名前と版を書いてください。** 同じ Skill でも、4 つの
ランタイムで挙動が違うことがあります。版は次のコマンドで分かります。

```bash
claude plugin list          # Claude Code
codex plugin list           # Codex
agy plugin list             # agy
```

Kiro CLI は導入したディレクトリの `.kiro/steering/ndf-policies.md` の冒頭に版が入ります。

## 応答の目安

メンテナーは 1 人です。返信までに数日かかることがあります。急ぎの場合は、その事情を
issue の本文に書いてください。

## セキュリティに関わること

**脆弱性は公開の issue に書かないでください。** 報告の方法は [SECURITY.md](./SECURITY.md)
にあります。
