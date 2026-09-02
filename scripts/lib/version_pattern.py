"""版数の書式を 1 か所に持つ。

説明文書の検査（`scripts/check-doc-staleness.py`）と、定義ファイルの検査
（`scripts/validate-runtime-plugins.sh` のヒアドキュメント）がどちらもこの書式を使う。

同じ規則を 2 つの定義が持つと、片方だけを直したときに一方の検査だけが新しい書式を
読める状態になる。接尾辞（`-dev.<連番>` / `-rc.<連番>`）を扱えるようにする直しが
2 件の課題に分かれ、同じ原因の同じ直しを 2 回行った経緯がある。

公開するのは版数の書式そのものと、そこから組み立てた 2 つの形だけである。版数を扱う
処理（比較・基底の取り出し）は移さない。説明文書の検査だけが使うものであり、移すと
定義ファイルの検査から読まれない関数がここへ集まる。
"""
from __future__ import annotations

import re

# 版数は接尾辞まで 1 つの値として拾う。`9.6.0-dev.1` を `9.6.0` と `1` に割らないためである。
VERSION = r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)"

# 突き合わせ先そのものの形を確かめるための、値だけの一致。
VERSION_VALUE = re.compile(VERSION)

# 定義ファイルの `description` に書かれた `(vX.Y.Z)` の形。
VERSION_IN_DESCRIPTION = re.compile(r"\(v" + VERSION + r"\)")
