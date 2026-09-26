"""共通設定 (config.yaml) のロードと設定のモデル。

テストケース YAML ではなく、対象環境・ロール別ログイン・Playwright/Runner 設定、
およびページ検査・スラッグ正規化・レポート生成のプロジェクト固有パラメータを保持する。

各節の形と型の変換は pydantic のモデルが持つ (#1142 の D8)。どの節も、書いていない項目と
値が null の項目は既定値になり、知らない項目は読み飛ばす。数は文字列の項目へ書いてもよい。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticUseDefault


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


class _Section(BaseModel):
    """設定の節の基底。null の項目は既定値、知らない項目は読み飛ばし、数は文字列の項目へ入れてよい。"""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _null_is_default(cls, data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


def _default_if_empty(value: Any) -> Any:
    """空 (空の配列・空の文字列) なら既定値を使う before validator の本体。"""
    if not value:
        raise PydanticUseDefault()
    return value


def _choice(value: Any, choices: tuple[str, ...], where: str) -> str:
    chosen = str(value).lower()
    if chosen not in choices:
        raise ValueError(f"{where} は {choices} のいずれかを指定してください (指定値: {chosen!r})")
    return chosen


# --- ブラウザ接続 ---------------------------------------------------

BrowserMode = Literal["local", "cdp-remote"]
BROWSER_MODES: tuple[BrowserMode, ...] = ("local", "cdp-remote")


class BrowserConfig(_Section):
    """ブラウザ接続設定。

    mode が空なら ``local``。cdp_endpoint が空文字列や空白のみの場合はデフォルト値
    ``http://localhost:9222`` にフォールバックする。
    """

    mode: BrowserMode = "local"
    cdp_endpoint: str = "http://localhost:9222"

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, value: Any) -> str:
        return _choice(_default_if_empty(value), BROWSER_MODES, "browser.mode")

    @field_validator("cdp_endpoint", mode="before")
    @classmethod
    def _endpoint(cls, value: Any) -> str:
        return _default_if_empty(str(value).strip() if value else "")

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "BrowserConfig":
        return cls.model_validate(raw)


# --- 接続/認証 -------------------------------------------------------

class BasicAuth(_Section):
    user: str = ""
    password: str = ""


class Login(_Section):
    path: str
    requires_basic_auth: bool = False
    fields: dict[str, str]
    fail_if_url_contains: str
    # ログイン送信ボタンを特定するためのプロジェクト固有セレクタ (CSS / role / text)。
    # auth fixture の _submit_login_form が「これ → role/type=submit フォールバック
    # → Password で Enter」の順で試す。空のままでも汎用フォールバックで通常はログインできる。
    submit_selectors: list[str] = Field(default_factory=list)


class Role(_Section):
    id: str
    label: str
    login: Login


# --- レポート設定 ---------------------------------------------------

class ReportConfig(_Section):
    title: str = "シナリオ E2E テスト 実施報告書"
    test_plan_link: str = "./test-plan.md"
    phase_labels: dict[int, str] = Field(default_factory=dict)


# --- Playwright / Runner -------------------------------------------

# Playwright ``record_har_mode`` に直接渡す値。"minimal" は request/response の
# メタデータのみ記録し、Basic 認証 + redirect が連続するページで navigation を
# abort させる race を回避する (Issue #62)。"full" は body も含めた完全な HAR、
# "none" は HAR を出力しない (= ``record_har_path`` を inject しない)。
HarMode = Literal["minimal", "full", "none"]
HAR_MODES: tuple[HarMode, ...] = ("minimal", "full", "none")


class PlaywrightConfig(_Section):
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

    @model_validator(mode="before")
    @classmethod
    def _flatten_sizes(cls, data: Any) -> Any:
        """config.yaml の ``viewport`` / ``video_size`` の ``width`` / ``height`` を平らな項目へ移す。"""
        if not isinstance(data, dict):
            return data
        flat = {k: v for k, v in data.items() if k not in ("viewport", "video_size")}
        for key, prefix in (("viewport", "viewport"), ("video_size", "video")):
            size = data.get(key) or {}
            for side in ("width", "height"):
                if size.get(side) is not None:
                    flat[f"{prefix}_{side}"] = size[side]
        return flat

    @field_validator("har_mode", mode="before")
    @classmethod
    def _har_mode(cls, value: Any) -> str:
        return _choice(value, HAR_MODES, "playwright.har_mode")

    @field_validator("video_format", mode="before")
    @classmethod
    def _video_format(cls, value: Any) -> str:
        return str(value).lower()

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "PlaywrightConfig":
        # 既定値はモデルの項目の既定値だけが持つ (from_raw の fallback と乖離させない。Codex Minor 6)
        return cls.model_validate(raw)

    @classmethod
    def defaults(cls) -> "PlaywrightConfig":
        """設定が完全に省略された場合の defaults。viewport=video_size=1280x720 で揃える。"""
        return cls()


class RunnerConfig(_Section):
    workers: int = 4
    testcases_dir: str = "./testcases"

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "RunnerConfig":
        return cls.model_validate(raw)


# --- accessibility / web vitals (v0.3.0) -----------------------------

class AccessibilityConfig(_Section):
    """axe-core 自動スキャンの設定 (page_role に応じて runner が自動実行)。空の配列は既定値に戻る。"""
    enabled: bool = True
    auto_roles: list[str] = Field(default_factory=lambda: [
        "lp", "list", "form", "dashboard", "cart", "checkout", "settings", "auth",
    ])
    tags: list[str] = Field(default_factory=lambda: [
        "wcag2a", "wcag2aa", "wcag21aa", "wcag22aa",
    ])
    # 検出した violations を testcase の FAIL 要因として扱うか (false なら情報出力のみ)
    fail_on_violations: bool = True

    _lists = field_validator("auto_roles", "tags", mode="before")(_default_if_empty)


class WebVitalsConfig(_Section):
    """Core Web Vitals 自動計測の設定 (page_role に応じて runner が自動実行)。空の配列は既定値に戻る。"""
    enabled: bool = True
    auto_roles: list[str] = Field(default_factory=lambda: [
        "lp", "list", "dashboard", "search",
    ])
    observe_ms: int = 5000
    # poor 判定が 1 件でもあれば testcase を FAIL とするか
    fail_on_poor: bool = True

    _lists = field_validator("auto_roles", mode="before")(_default_if_empty)


# --- body_check (PHP / SSR エラー検出, v0.4.0) ----------------------

class BodyCheckConfig(_Section):
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
    パターンの配列は、省略か null なら既定値、明示的な空の配列なら空のまま (カテゴリの無効化)。
    """

    enabled: bool = True
    fatal_patterns: list[str] = Field(default_factory=lambda: [
        "Fatal error",
        "Uncaught",
        "Parse error",
    ])
    warning_patterns: list[str] = Field(default_factory=lambda: [
        "STRICT:",
        "Warning:",
        "Notice:",
        "Deprecated:",
    ])
    # 文字数ベースの head 切り出し閾値 (code points)。PLAN18 のフィールド名は
    # ``warning_head_bytes`` だったが、説明文は「先頭 300 文字」と書かれており
    # 矛盾していた。実用上は文字数の方が日本語ページで安定するため採用。
    # 旧名 ``warning_head_bytes`` も alias として受理する。
    warning_head_chars: int = 300
    not_found_patterns: list[str] = Field(default_factory=lambda: [
        "File not found",
    ])
    fail_on_match: bool = True

    @model_validator(mode="before")
    @classmethod
    def _head_bytes_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("warning_head_chars") is None and "warning_head_bytes" in data:
            data = {**data, "warning_head_chars": data["warning_head_bytes"]}
        return data


# --- ルート ---------------------------------------------------------

class Config(_Section):
    base_url: str
    basic_auth: BasicAuth
    verify_tls: bool
    roles: dict[str, Role]
    playwright: PlaywrightConfig
    runner: RunnerConfig
    report: ReportConfig
    config_path: Path  # 設定ファイルの絶対パス（testcases_dir の解決基点）
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    # docs/checklists/checklist-common.md C8/C9 の境界曖昧さに対応する「除外」設定。
    # console.error / pageerror の本文がいずれかの正規表現にマッチした場合は
    # 集計から除外し FAIL を抑制する。3rd party の既知 warning などを許容するための
    # 抜け穴。空 (デフォルト) なら従来どおり 1 件で FAIL。
    tolerated_console_errors: list[str] = Field(default_factory=list)
    tolerated_page_errors: list[str] = Field(default_factory=list)
    # accessibility / web_vitals 自動実行 (page_role に応じて runner が判定)
    accessibility: AccessibilityConfig = Field(default_factory=AccessibilityConfig)
    web_vitals: WebVitalsConfig = Field(default_factory=WebVitalsConfig)
    # PHP / SSR ページ本文エラー検出 (v0.4.0, opt-in)
    body_check: BodyCheckConfig = Field(default_factory=BodyCheckConfig)

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
        basic_auth = BasicAuth.model_validate(target.get("basic_auth") or {})
        roles = {rid: _role_from_raw(rid, r) for rid, r in (raw.get("roles") or {}).items()}

        cfg = cls.model_validate({
            "base_url": target["base_url"].rstrip("/"),
            "basic_auth": basic_auth,
            "verify_tls": raw.get("verify_tls", False),
            "roles": roles,
            "playwright": raw.get("playwright") or {},
            "runner": raw.get("runner") or {},
            "report": raw.get("report") or {},
            "config_path": config_path,
            "browser": raw.get("browser") or {},
            "tolerated_console_errors": raw.get("tolerated_console_errors") or [],
            "tolerated_page_errors": raw.get("tolerated_page_errors") or [],
            "accessibility": raw.get("accessibility") or {},
            "web_vitals": raw.get("web_vitals") or {},
            "body_check": raw.get("body_check") or {},
        })

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
    return Role.model_validate({"id": rid, "label": raw.get("label", rid), "login": raw["login"]})


def _report_from_raw(raw: dict[str, Any]) -> ReportConfig:
    return ReportConfig.model_validate(raw)


def _accessibility_from_raw(raw: dict[str, Any]) -> AccessibilityConfig:
    return AccessibilityConfig.model_validate(raw)


def _web_vitals_from_raw(raw: dict[str, Any]) -> WebVitalsConfig:
    return WebVitalsConfig.model_validate(raw)


def _body_check_from_raw(raw: dict[str, Any]) -> BodyCheckConfig:
    """``body_check`` セクションを ``BodyCheckConfig`` に変換する。

    - キーが **省略** されている (か null の) 場合は既定値を採用する
      (config を書かなくても PHP 系のデフォルトパターンが効くようにするため)。
    - キーが **明示的に空リスト** で書かれている場合はそのまま空リストにする
      (default を上書きしてカテゴリを無効化したい場合の挙動)。
    """
    return BodyCheckConfig.model_validate(raw)
