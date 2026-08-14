"""Playwright 録画 webm → Google Drive 互換 mp4 への変換。

Drive のストリーミング再生で「処理中」になりにくい設定:
  - H.264 High profile / Level 4.0 / yuv420p / bt709
  - 30 fps CFR (Constant Frame Rate)
  - 60 frames keyframe interval (2 sec at 30fps)
  - AAC LC stereo 48kHz 128kbps (無音でも音声トラックは必須)
  - +faststart で moov atom を先頭配置 → プログレッシブ再生

依存: imageio-ffmpeg (静的 ffmpeg バイナリ同梱の Python パッケージ)
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _ffmpeg_args(webm: Path, mp4: Path) -> list[str]:
    """Drive 互換 mp4 への変換用 ffmpeg 引数を組み立てる。"""
    return [
        "-y",
        "-i", str(webm),
        # 無音 AAC を映像と並行して生成 (一部プレイヤは音声トラック必須)
        "-f", "lavfi", "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        # --- 映像 ---
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level", "4.0",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        "-r", "30",                    # 固定フレームレート
        "-fps_mode", "cfr",            # 旧 -vsync の後継
        "-g", "60",                    # キーフレーム間隔 2秒
        "-keyint_min", "30",
        "-sc_threshold", "0",          # シーン検出キーフレーム無効
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        # --- 音声 (無音) ---
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-shortest",                   # 映像終了で打ち切り
        # --- ストリーミング向け ---
        "-movflags", "+faststart",
        str(mp4),
    ]


def convert_webm_to_mp4(webm: Path, mp4: Path, *, timeout_sec: int = 300) -> Path | None:
    """webm を Drive 互換 mp4 に変換。成功時は mp4 のパス、失敗時は None。"""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

    try:
        subprocess.run(
            [ffmpeg, *_ffmpeg_args(webm, mp4)],
            check=True, capture_output=True, timeout=timeout_sec,
        )
    except Exception:
        return None
    if not mp4.exists() or mp4.stat().st_size == 0:
        return None
    return mp4
