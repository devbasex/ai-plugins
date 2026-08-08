@echo off
rem scenario-test ランタイムのワンコマンドランチャ (Windows 版)。
rem
rem 使い方:
rem   run.bat                                 全テスト実行
rem   run.bat -k test_login                   nodeid フィルタ
rem   run.bat --pwk-overlay --pwk-drive-folder=<id>
rem   run.bat --help                          help を表示
rem
rem 動作:
rem   1) 自身が置かれているディレクトリを RUNTIME_DIR とし、CWD をそこに固定
rem   2) .venv\ が無ければ `uv sync` + `playwright install chromium` を実行
rem   3) `uv run pytest --pwk-config=%PWK_CONFIG%` を引数素通しで起動
rem
rem 注意:
rem   cmd.exe ネイティブの %* 展開ではスペースを含む引数 (例: -k "test login") の
rem   クォートが正しく保持されません。スペース込みの引数を渡したい場合は
rem   PowerShell から実行するか、引数を quote 不要な形に書き換えてください:
rem     PowerShell> & .\run.bat -k 'test_login'      (アンダースコア化を推奨)
rem     PowerShell> & .\run.bat -m 'page_role'

setlocal EnableExtensions EnableDelayedExpansion

rem このスクリプト自身が置かれているディレクトリ
set "RUNTIME_DIR=%~dp0"
rem 末尾の \ を除去
if "%RUNTIME_DIR:~-1%"=="\" set "RUNTIME_DIR=%RUNTIME_DIR:~0,-1%"

rem --- help -------------------------------------------------------
if "%~1"=="-h" goto :show_help
if "%~1"=="--help" goto :show_help
goto :run_steps

:show_help
echo scenario-test ランタイムランチャ
echo.
echo 使い方:
echo   run.bat [pytest 引数...]
echo.
echo 主な追加引数 (pytest にそのまま転送):
echo   --pwk-config ^<path^>      scenario.config.yaml のパス (env PWK_CONFIG でも可)
echo   --pwk-out-dir ^<path^>     成果物出力先 (default: ./reports/^<run-id^>/)
echo   --pwk-no-evidence        HAR / trace / 動画 を OFF
echo   --pwk-har-mode {minimal,full,none}
echo                            HAR 録画モード (default: minimal)
echo   --pwk-overlay            動画に赤丸カーソル + 字幕 (旧名 HUD) を焼き込む
echo   --pwk-drive-folder ^<id^>  終了後に成果物を Google Drive にアップロード
echo   -k ^<expr^>                nodeid 部分一致フィルタ
echo   -m ^<expr^>                marker フィルタ (page_role / role / phase 等)
echo   --headed                 ブラウザを画面表示 (debug 用)
echo.
echo 環境変数:
echo   PWK_CONFIG               scenario.config.yaml のパス
echo.
exit /b 0

:run_steps

rem --- 1) uv の存在確認 ------------------------------------------
where uv >nul 2>&1
if errorlevel 1 (
  echo [run.bat] uv が見つかりません。
  echo [run.bat] https://docs.astral.sh/uv/getting-started/installation/ を
  echo [run.bat] 参照してインストールしてから再実行してください。
  exit /b 1
)

rem --- 2) 初回のみ uv sync + playwright install ------------------
if not exist "%RUNTIME_DIR%\.venv" (
  echo [run.bat] 初回セットアップ: uv sync ^(%RUNTIME_DIR%^)
  pushd "%RUNTIME_DIR%"
  uv sync
  if errorlevel 1 (
    popd
    echo [run.bat] uv sync に失敗しました。
    exit /b 1
  )
  echo [run.bat] 初回セットアップ: playwright install chromium
  uv run playwright install chromium
  if errorlevel 1 (
    echo [run.bat] WARN: playwright install chromium に失敗しました。
    echo [run.bat]       オフライン環境では PLAYWRIGHT_BROWSERS_PATH を共有
    echo [run.bat]       キャッシュへ向ける運用を検討してください。
  )
  popd
)

rem --- 3) pytest 実行 --------------------------------------------
if "%PWK_CONFIG%"=="" set "PWK_CONFIG=.\scenario.config.yaml"

pushd "%RUNTIME_DIR%"
uv run pytest --pwk-config="%PWK_CONFIG%" %*
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
