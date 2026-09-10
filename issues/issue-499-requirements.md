# #499 AGENTS.md を定義（ナビゲーション + ポリシー）へ戻す

## 目的

`AGENTS.md` の役割は「ナビゲーション + ポリシー（軽量）」と定義されているが、478 行のうち
312 行（65%）が版数の扱いに費やされている。**この 312 行は手順と実測であって、判断の基準では
ない。** 取得元の登録の振る舞い・clone の refspec・CLI ごとの副コマンドの有無・隔離した設定
ディレクトリでの確かめ方は、定義に照らせば `docs/` の領分になる。

5 版で 105 行増えており、減った版が 1 つも無い。分割の基準（501 行以上）まで残り 23 行である。

## 決めたこと

### 1. 仕分けの基準

**判断の基準（何を選ぶか）は残し、手順と実測（どう動くか）は移す。**

| 中身 | 置き場所 |
| --- | --- |
| チャネルを 2 つに分けること、正式版を既定ブランチへ置くこと | `AGENTS.md` |
| 接尾辞を付ける方針、版を上げる時期（まとまり単位） | `AGENTS.md` |
| 接尾辞の形の一覧（版数の例を並べた表） | **移す** |
| マイルストーンの名前の付け方、版を決める唯一の箇所 | `AGENTS.md` |
| 同名の取得元を登録しないこと | `AGENTS.md` |
| clone の fetch の refspec、CLI ごとの副コマンドの差 | **移す** |
| 取得元の登録と導入の手順、ランタイムごとのコマンド例 | **移す** |
| 版数を持つ 15 箇所の一覧と、検査に載らず手で直す箇所 | **移す** |
| 隔離した設定ディレクトリでの確かめ方、実測のコンソール出力 | **移す** |

### 2. 移す先

**`docs/versioning-and-distribution.md` を新設する。**

`docs/plugin-development-guide.md` は 363 行で、312 行を足すと分割の基準を超える。
また、この文書は「プラグインを作る」手順を持ち、版と配布は別の主題である。

### 3. 版数の扱いの正本

**正本は `docs/versioning-and-distribution.md` にする。** `docs/plugin-development-guide.md` の
「バージョン管理」と「利用者が過去の版へ戻る」は同じ主題を扱うため、正本へ集約し、残りは
リンクへ置き換える。#498 が定めた「正本を決めて残りをリンクに置き換える」の適用である。

### 4. 検査が読む先

`scripts/check-doc-staleness.py` は `AGENTS.md` の 2 箇所を読む。

| 検査が読む記載 | 移動後の場所 |
| --- | --- |
| 「NDFプラグインについて」の版数 | `AGENTS.md`（ナビゲーションとして残る） |
| 「版の付け方と開発版の配布」節の版数 | **`docs/versioning-and-distribution.md`** |

**検査の読む先も一緒に動かす。** 記載を消すだけでは、位置を決める語が見つからず読み取れない
こととして落ちる。移設は読む先のファイルだけでは済まず、報告先のキーと位置決めの見出しも
動く。触る箇所は [issue-499-design.md](issue-499-design.md) の「検査が読む先」が持つ。

## 受け入れ条件

- [ ] `AGENTS.md` の版数の扱いが `docs/versioning-and-distribution.md` へ移り、`AGENTS.md`
      からはリンクで辿れる
- [ ] `AGENTS.md` が 300 行以下になる
- [ ] 移した記述が失われていない（移動前の段落と移動後の章の対応表を設計の「移動元と移動先の
      対応」が持ち、表・実測値・コマンド例がその対応どおりに揃っている）
- [ ] `docs/plugin-development-guide.md` の「バージョン管理」と「利用者が過去の版へ戻る」が、
      正本へのリンクに置き換わっている
- [ ] `python3 scripts/check-doc-staleness.py` が終了コード 0（読む先・報告先・位置決めの
      見出しを動かした分の変更を含む）
- [ ] `AGENTS.md` に囲んだ版数が「主要プラグインです（v<版>）」の 1 箇所だけ残る
- [ ] `uv run --with pytest pytest scripts/tests -q` が通る
- [ ] `python3 scripts/check-markdown-links.py --root .` が終了コード 0
- [ ] `python3 scripts/check-doc-line-limit.py` が終了コード 0
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0

## 対象範囲

**含む** — `AGENTS.md` の版数の扱い、移す先の新設、`docs/plugin-development-guide.md` の
重複の解消、`scripts/check-doc-staleness.py` の読む先とそのテスト。

**含まない**

- 記述を減らすこと（**移すのであって減らさない**。版数の扱いは実測に基づく知識で、失うと
  次の配布で同じことを調べ直す）
- `CLAUDE.md` の版ごとの段落の移動（判断の理由を持つ履歴で、役割が違う）
- `README.md` 22 本の書き直し（#500）
- 節ごとの割合の偏りを機械で見る仕組みの新設（**役割は機械では決まらない**）

## 検証手段

| 何を確かめるか | コマンド |
| --- | --- |
| 版数の記載 | `python3 scripts/check-doc-staleness.py` |
| 検査の振る舞い | `uv run --with pytest pytest scripts/tests -q` |
| リンク | `python3 scripts/check-markdown-links.py --root .` |
| 分量 | `python3 scripts/check-doc-line-limit.py` |
| 配布物 | `bash scripts/validate-runtime-plugins.sh` |

## 境界

| 区分 | 中身 |
| --- | --- |
| 常に行う | 既存テストの実行、移した記述の突き合わせ |
| 確認してから行う | 検査が読む記載の書式の変更、`AGENTS.md` の他の節への手入れ |
| 行わない | 記述の削減、`CLAUDE.md` の履歴への手入れ |
