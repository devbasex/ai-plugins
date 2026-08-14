"""共通設定 (config.yaml) のロードとデータクラス。

テストケース YAML ではなく、対象環境・ロール別ログイン・Playwright/Runner 設定、
およびページ検査・スラッグ正規化・レポート生成のプロジェクト固有パラメータを保持する。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


# ---------------------------------------------------------------------------
# 環境変数展開 (Codex Major 4)
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env_in_str(s: str) -> str:
    """文字列中の ${VAR} / ${VAR:-default} を環境変数で展開する。"""
    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        val = os.environ.get(name)
        if val is None:
            if default is None:
                raise ValueError(
                    f"環境変数 ${{{name}}} が未定義です "
                    "(default 指定 ${VAR:-default} または env を設定してください)"
                )
            return default
        return val
    return _ENV_RE.sub(repl, s)


def _expand_env(value: Any) -> Any:
    """dict / list / str を再帰的に走査して ${VAR} を展開する。"""
    if isinstance(value, str):
        return _expand_env_in_str(value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


# --- ブラウザ接続 ---------------------------------------------------

BrowserMode = Literal["local", "cdp-remote"]
BROWSER_MODES: tuple[BrowserMode, ...] = ("local", "cdp-remote")


@dataclass
class BrowserConfig:
    """ブラウザ接続設定。

    cdp_endpoint が空文字列や空白のみの場合はデフォルト値
    ``http://localhost:9222`` にフォールバックする。
    """

    mode: BrowserMode = "local"
    cdp_endpoint: str = "http://localhost:9222"

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "BrowserConfig":
        base = cls()
        mode_raw = str(raw.get("mode") or base.mode).lower()
        if mode_raw not in BROWSER_MODES:
            raise ValueError(
                f"browser.mode は {BROWSER_MODES} のいずれかを指定してください "
                f"(指定値: {mode_raw!r})"
            )
        mode: BrowserMode = mode_raw  # type: ignore[assignment]
        # cdp_endpoint: 空文字列・空白のみの場合はデフォルト値にフォールバック
        cdp_raw = raw.get("cdp_endpoint")
        cdp_endpoint = str(cdp_raw).strip() if cdp_raw else ""
        if not cdp_endpoint:
            cdp_endpoint = base.cdp_endpoint
        return cls(
            mode=mode,
            cdp_endpoint=cdp_endpoint,
        )


# --- 接続/認証 -------------------------------------------------------

@dataclass
class BasicAuth:
    user: str
    password: str


@dataclass
class Login:
    path: str
    requires_basic_auth: bool
    fields: dict[str, str]
    fail_if_url_contains: str
    # ログイン送信ボタンを特定するためのプロジェクト固有セレクタ (CSS / role / text)。
    # auth fixture の _submit_login_form が「これ → role/type=submit フォールバック
    # → Password で Enter」の順で試す。空のままでも汎用フォールバックで通常はログインできる。
    submit_selectors: list[str] = field(default_factory=list)


@dataclass
class Role:
    id: str
    label: str
    login: Login


# --- レポート設定 ---------------------------------------------------

@dataclass
class ReportConfig:
    title: str = "シナリオ E2E テスト 実施報告書"
    test_plan_link: str = "./test-plan.md"
    phase_labels: dict[int, str] = field(default_factory=dict)


# --- Playwright / Runner -------------------------------------------

# Playwright ``record_har_mode`` に直接渡す値。"minimal" は request/response の
# メタデータのみ記録し、Basic 認証 + redirect が連続するページで navigation を
# abort させる race を回避する (Issue #62)。"full" は body も含めた完全な HAR、
# "none" は HAR を出力しない (= ``record_har_path`` を inject しない)。
HarMode = Literal["minimal", "full", "none"]
HAR_MODES: tuple[HarMode, ...] = ("minimal", "full", "none")


@dataclass
class PlaywrightConfig:
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 720
    slow_mo_ms: int = 0
    video_width: int = 1280
    video_height: int = 720
    navigation_timeout_ms: int = 30000
    # 各ステップ遷移後の表示維持時間 (動画でじっくり見せるため)
    step_delay_ms: int = 1800
    # 動画にカーソル＋字幕オーバーレイを焼き込む (true 推奨)
    enable_overlay: bool = True
    # Playwright Trace (trace.zip) を出力する。クリック箇所のハイライト・
    # DOM スナップショット・コンソール・ネットワークなどを `playwright show-trace`
    # で対話的に確認できる。生成物が大きく (数MB〜) なるので必要時のみ。
    enable_trace: bool = True
    # 録画後の動画フォーマット: "webm" (Playwright 既定) | "mp4" (H.264 変換)
    # mp4 は Google Drive プレビュアで再生互換性が高い。
    video_format: str = "mp4"
    # HAR 録画モード (Issue #62)。Playwright >= 1.30 で導入された
    # ``record_har_mode`` に対応する。
    # - "minimal" (default): メタデータのみ記録。Basic 認証 + redirect が混在
    #   するページで ``record_har_path`` 起因の ERR_ABORTED race を回避する。
    # - "full": Playwright 既定の full HAR (body + content)。
    # - "none": HAR を一切出力しない (= ``record_har_path`` を inject しない)。
    har_mode: HarMode = "minimal"

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "PlaywrightConfig":
        # dataclass の default を真実の源 (single source of truth) とする。
        # fallback 値を base = cls() から参照することで、dataclass default と
        # from_raw() の fallback が乖離するバグを防ぐ (Codex Minor 6)。
        base = cls()
        viewport = raw.get("viewport") or {}
        video_size = raw.get("video_size") or {}
        har_mode_raw = str(raw.get("har_mode", base.har_mode)).lower()
        if har_mode_raw not in HAR_MODES:
            raise ValueError(
                f"playwright.har_mode は {HAR_MODES} のいずれかを指定してください "
                f"(指定値: {har_mode_raw!r})"
            )
        har_mode: HarMode = har_mode_raw  # type: ignore[assignment]
        return cls(
            headless=bool(raw.get("headless", base.headless)),
            viewport_width=int(viewport.get("width", base.viewport_width)),
            viewport_height=int(viewport.get("height", base.viewport_height)),
            slow_mo_ms=int(raw.get("slow_mo_ms", base.slow_mo_ms)),
            video_width=int(video_size.get("width", base.video_width)),
            video_height=int(video_size.get("height", base.video_height)),
            navigation_timeout_ms=int(raw.get("navigation_timeout_ms", base.navigation_timeout_ms)),
            step_delay_ms=int(raw.get("step_delay_ms", base.step_delay_ms)),
            enable_overlay=bool(raw.get("enable_overlay", base.enable_overlay)),
            enable_trace=bool(raw.get("enable_trace", base.enable_trace)),
            video_format=str(raw.get("video_format", base.video_format)).lower(),
            har_mode=har_mode,
        )

    @classmethod
    def defaults(cls) -> "PlaywrightConfig":
        """設定が完全に省略された場合の defaults。viewport=video_size=1280x720 で揃える。"""
        return cls()


@dataclass
class RunnerConfig:
    workers: int = 4
    testcases_dir: str = "./testcases"

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "RunnerConfig":
        return cls(
            workers=int(raw.get("workers", 4)),
            testcases_dir=str(raw.get("testcases_dir", "./testcases")),
        )


# --- accessibility / web vitals (v0.3.0) -----------------------------

@dataclass
class AccessibilityConfig:
    """axe-core 自動スキャンの設定 (page_role に応じて runner が自動実行)。"""
    enabled: bool = True
    auto_roles: list[str] = field(default_factory=lambda: [
        "lp", "list", "form", "dashboard", "cart", "checkout", "settings", "auth",
    ])
    tags: list[str] = field(default_factory=lambda: [
        "wcag2a", "wcag2aa", "wcag21aa", "wcag22aa",
    ])
    # 検出した violations を testcase の FAIL 要因として扱うか (false なら情報出力のみ)
    fail_on_violations: bool = True


@dataclass
class WebVitalsConfig:
    """Core Web Vitals 自動計測の設定 (page_role に応じて runner が自動実行)。"""
    enabled: bool = True
    auto_roles: list[str] = field(default_factory=lambda: [
        "lp", "list", "dashboard", "search",
    ])
    observe_ms: int = 5000
    # poor 判定が 1 件でもあれば testcase を FAIL とするか
    fail_on_poor: bool = True


# --- body_check (PHP / SSR エラー検出, v0.4.0) ----------------------

@dataclass
class BodyCheckConfig:
    """ページ本文の文字列マッチ検出 (PHP / SSR プロジェクト向け)。

    JavaScript ランタイム由来の console.error / pageerror では拾えない、
    サーバ側で HTML 本文に直接出力された "Fatal error" / "Warning:" 等の
    エラー文字列を、Playwright の ``page.on("response", ...)`` を介して
    検出する。

    - ``fatal_patterns``: HTML 本文全体に対する substring match。1 つでも
      含まれれば violation。
    - ``warning_patterns``: 本文の **先頭 ``warning_head_chars`` 文字** に
      対する substring match。本文中の説明文に含まれる "Notice:" 等は許容し、
      ページ最上段への漏れだけを拾う。
      (PLAN18 の説明文は「先頭 300 文字」、code point ベース。日本語ページで
      300 bytes だと先頭 1〜2 行しか見えず実用にならないため、文字数を採用。)
    - ``not_found_patterns``: 本文全体への substring match。
    - ``fail_on_match``: True なら violation 検出時に ``pytest.fail``。
      False なら情報収集のみ (report.md / body_check.jsonl には記録)。

    default は ``enabled=True`` + PHP 系のフロント漏れ検出パターンを内蔵。
    config.yaml を書かなくてもまず PHP プロジェクトで素直に動く。
    """

    enabled: bool = True
    fatal_patterns: list[str] = field(default_factory=lambda: [
        "Fatal error",
        "Uncaught",
        "Parse error",
    ])
    warning_patterns: list[str] = field(default_factory=lambda: [
        "STRICT:",
        "Warning:",
        "Notice:",
        "Deprecated:",
    ])
    # 文字数ベースの head 切り出し閾値 (code points)。PLAN18 のフィールド名は
    # ``warning_head_bytes`` だったが、説明文は「先頭 300 文字」と書かれており
    # 矛盾していた。実用上は文字数の方が日本語ページで安定するため採用。
    warning_head_chars: int = 300
    not_found_patterns: list[str] = field(default_factory=lambda: [
        "File not found",
    ])
    fail_on_match: bool = True


# --- ルート ---------------------------------------------------------

@dataclass
class Config:
    base_url: str
    basic_auth: BasicAuth
    verify_tls: bool
    roles: dict[str, Role]
    playwright: PlaywrightConfig
    runner: RunnerConfig
    report: ReportConfig
    config_path: Path  # 設定ファイルの絶対パス（testcases_dir の解決基点）
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    # docs/checklists/checklist-common.md C8/C9 の境界曖昧さに対応する「除外」設定。
    # console.error / pageerror の本文がいずれかの正規表現にマッチした場合は
    # 集計から除外し FAIL を抑制する。3rd party の既知 warning などを許容するための
    # 抜け穴。空 (デフォルト) なら従来どおり 1 件で FAIL。
    tolerated_console_errors: list[str] = field(default_factory=list)
    tolerated_page_errors: list[str] = field(default_factory=list)
    # accessibility / web_vitals 自動実行 (page_role に応じて runner が判定)
    accessibility: AccessibilityConfig = field(default_factory=AccessibilityConfig)
    web_vitals: WebVitalsConfig = field(default_factory=WebVitalsConfig)
    # PHP / SSR ページ本文エラー検出 (v0.4.0, opt-in)
    body_check: BodyCheckConfig = field(default_factory=BodyCheckConfig)

    @property
    def testcases_dir(self) -> Path:
        d = Path(self.runner.testcases_dir)
        if not d.is_absolute():
            d = self.config_path.parent / d
        return d.resolve()

    def role(self, role_id: str) -> Role:
        if role_id not in self.roles:
            raise KeyError(f"未定義のロール: {role_id}. roles 設定を確認してください。")
        return self.roles[role_id]

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            raise FileNotFoundError(
                f"設定ファイルが見つかりません: {path}\n"
                "templates/scenario.config.yaml をコピーして作成してください。"
            )
        with path.open("r", encoding="utf-8") as fp:
            raw = yaml.safe_load(fp)
        if not isinstance(raw, dict):
            raise ValueError(
                f"scenario.config.yaml の中身が空または辞書ではありません: {path}\n"
                "templates/scenario.config.yaml をコピーして必要項目を埋めてください。"
            )
        raw = _expand_env(raw)
        return cls._from_dict(raw, config_path=path.resolve())

    @classmethod
    def _from_dict(cls, raw: dict[str, Any], *, config_path: Path) -> "Config":
        target = raw["target"]
        # basic_auth は省略可能 (サイトに Basic 認証が掛かっていない場合)。
        # 省略時は空 BasicAuth を使い、role 側で `requires_basic_auth: true` を
        # 指定したテストケースだけが basic_auth ヘッダを要求する設計。
        ba_raw = target.get("basic_auth") or {}
        basic_auth = BasicAuth(
            user=str(ba_raw.get("user", "")),
            password=str(ba_raw.get("password", "")),
        )
        roles = {rid: _role_from_raw(rid, r) for rid, r in (raw.get("roles") or {}).items()}

        cfg = cls(
            base_url=target["base_url"].rstrip("/"),
            basic_auth=basic_auth,
            verify_tls=bool(raw.get("verify_tls", False)),
            roles=roles,
            playwright=PlaywrightConfig.from_raw(raw.get("playwright") or {}),
            runner=RunnerConfig.from_raw(raw.get("runner") or {}),
            report=_report_from_raw(raw.get("report") or {}),
            config_path=config_path,
            browser=BrowserConfig.from_raw(raw.get("browser") or {}),
            tolerated_console_errors=list(raw.get("tolerated_console_errors") or []),
            tolerated_page_errors=list(raw.get("tolerated_page_errors") or []),
            accessibility=_accessibility_from_raw(raw.get("accessibility") or {}),
            web_vitals=_web_vitals_from_raw(raw.get("web_vitals") or {}),
            body_check=_body_check_from_raw(raw.get("body_check") or {}),
        )

        # fail-fast: requires_basic_auth=True なロールが宣言されているのに
        # basic_auth.user が空ならば実行時に HTTP 401 で必ず落ちる。先に検出して
        # 設定不備として ValueError を投げる (Maj-4)。
        for role in cfg.roles.values():
            if role.login.requires_basic_auth and not basic_auth.user:
                raise ValueError(
                    f"role '{role.id}' は requires_basic_auth=True だが、"
                    f"target.basic_auth.user が空 (config.yaml を確認してください)"
                )

        return cfg


def _role_from_raw(rid: str, raw: dict[str, Any]) -> Role:
    login = raw["login"]
    return Role(
        id=rid,
        label=str(raw.get("label", rid)),
        login=Login(
            path=login["path"],
            requires_basic_auth=bool(login.get("requires_basic_auth", False)),
            fields=dict(login["fields"]),
            fail_if_url_contains=login["fail_if_url_contains"],
            submit_selectors=list(login.get("submit_selectors") or []),
        ),
    )


def _report_from_raw(raw: dict[str, Any]) -> ReportConfig:
    labels_raw = raw.get("phase_labels") or {}
    return ReportConfig(
        title=str(raw.get("title", "シナリオ E2E テスト 実施報告書")),
        test_plan_link=str(raw.get("test_plan_link", "./test-plan.md")),
        phase_labels={int(k): str(v) for k, v in labels_raw.items()},
    )


def _accessibility_from_raw(raw: dict[str, Any]) -> AccessibilityConfig:
    base = AccessibilityConfig()
    return AccessibilityConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        auto_roles=list(raw.get("auto_roles") or base.auto_roles),
        tags=list(raw.get("tags") or base.tags),
        fail_on_violations=bool(raw.get("fail_on_violations", base.fail_on_violations)),
    )


def _web_vitals_from_raw(raw: dict[str, Any]) -> WebVitalsConfig:
    base = WebVitalsConfig()
    return WebVitalsConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        auto_roles=list(raw.get("auto_roles") or base.auto_roles),
        observe_ms=int(raw.get("observe_ms", base.observe_ms)),
        fail_on_poor=bool(raw.get("fail_on_poor", base.fail_on_poor)),
    )


def _body_check_from_raw(raw: dict[str, Any]) -> BodyCheckConfig:
    """``body_check`` セクションを ``BodyCheckConfig`` に変換する。

    - キーが **省略** されている場合は dataclass の default 値を採用する
      (config を書かなくても PHP 系のデフォルトパターンが効くようにするため)。
    - キーが **明示的に空リスト** で書かれている場合はそのまま空リストにする
      (default を上書きしてカテゴリを無効化したい場合の挙動)。
    """
    base = BodyCheckConfig()

    def _patterns(key: str, default: list[str]) -> list[str]:
        if key not in raw:
            return list(default)
        value = raw.get(key)
        if value is None:
            return list(default)
        return [str(s) for s in value]

    # ``warning_head_chars`` を新フィールド名として採用。旧名 ``warning_head_bytes``
    # も alias として受理する (PLAN18 がフィールド名と説明文で矛盾していた経緯)。
    head_chars = raw.get("warning_head_chars")
    if head_chars is None:
        head_chars = raw.get("warning_head_bytes", base.warning_head_chars)

    return BodyCheckConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        fatal_patterns=_patterns("fatal_patterns", base.fatal_patterns),
        warning_patterns=_patterns("warning_patterns", base.warning_patterns),
        warning_head_chars=int(head_chars),
        not_found_patterns=_patterns("not_found_patterns", base.not_found_patterns),
        fail_on_match=bool(raw.get("fail_on_match", base.fail_on_match)),
    )
