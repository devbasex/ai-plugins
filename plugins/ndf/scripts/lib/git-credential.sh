#!/usr/bin/env bash
# NDF plugin: credential helper が応答しない環境で git を通すための退避の値（#524）。
#
#   . <プラグインルート>/scripts/lib/git-credential.sh
#   ndf_git_credential_fallback_args      # 1 行 1 語で退避のオプションを出す
#
# **`gh` は認証済みなのに `git push` だけが落ちる環境がある**（#524）。helper が
# 応答を返さないためで、`gh auth git-credential` を helper として渡すと通る。
#
# **空の値を先に置くことが退避の本体である。** `credential.helper` は複数の値を
# 持てる設定で、`git` は宣言された順に問い合わせる。空の値だけが一覧を空へ戻す。
# 先に置かないと、応答しない helper が先に当たり続け、足した経路へ到達しない
# （実測）。
#
# **この共通層が持つのは値だけである。** 失敗→退避→1 度だけ再試行という振る舞いは
# 退避する側（`cross-refactoring` の `gitfacts.py`）が 1 か所で持つ。呼び出し側の
# 無いシェル関数をここへ置くと、同じ分岐がシェルと実装の 2 か所に分かれ、片方だけが
# 直る余地が残る。**手順書（`pr` / `fix`）はこのファイルを読み込まない**（任意の
# リポジトリで成立させるため、同じ値のコマンドをそのまま案内する）。

# 退避に使う `git` のオプションを 1 行 1 語で出す。**この 1 か所だけが値を持つ。**
# 手順書と実装が別々に同じ文字列を持つと、片方だけが更新される。
ndf_git_credential_fallback_args() {
  printf '%s\n' -c 'credential.helper=' \
                 -c 'credential.helper=!gh auth git-credential'
}
