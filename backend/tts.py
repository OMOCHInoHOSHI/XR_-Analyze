"""
鑑定文の読み上げ(音声合成)。

現状は macOS 同梱の `say` コマンドで WAV を合成するだけだが、後から
VOICEVOX 等のローカル音声合成エンジンに差し替えられるよう、合成処理を
本モジュールに閉じ、呼び出し側は `synthesize()` だけを見ればよい形にする。
バックエンドの選択は `config.TTS_BACKEND` で行う。

方式の選定経緯 (VOICEVOX 等を採らなかった理由を含む):
docs/adr/0025-inspector-voice-readout.md
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import config


class TTSUnavailable(RuntimeError):
    """音声合成が実行できない(未対応環境・失敗・タイムアウトなど)ことを表す。"""


def synthesize(text: str) -> bytes:
    """
    本文から WAV バイト列を合成して返す。`config.TTS_BACKEND` で分岐する。
    """
    backend = config.TTS_BACKEND
    if backend == "off":
        raise TTSUnavailable("読み上げは無効化されています (TTS_BACKEND=off)")
    if backend == "say":
        return _say(text)
    raise TTSUnavailable(f"未知の TTS_BACKEND: {backend}")


def _say(text: str) -> bytes:
    """
    macOS の `say` コマンドで WAV を合成する。

    - 本文は必ず標準入力(`-f -`)で渡す。コマンド引数にすると本文が
      "-r 999 ..." のようにハイフンで始まる場合にオプションと誤認されうる
      ことを実測で確認したため。
    - `-o -` (標準出力への書き出し)は `Opening output file failed: fmt?` で
      失敗することを実測で確認したため、一時ファイル経由で書き出す。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        out = Path(tmp_dir) / "speak.wav"
        cmd = [
            "say",
            "-v", config.TTS_VOICE,
            "-r", str(config.TTS_RATE),
            "-f", "-",
            "--data-format=LEI16@22050",
            "-o", str(out),
        ]
        try:
            subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                timeout=config.TTS_TIMEOUT_SEC,
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as e:
            # macOS 以外には `say` が存在しない
            raise TTSUnavailable("読み上げコマンド (say) が見つかりません (macOS 専用です)") from e
        except subprocess.CalledProcessError as e:
            raise TTSUnavailable(f"読み上げの合成に失敗しました: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise TTSUnavailable("読み上げの合成がタイムアウトしました") from e

        # 一時ディレクトリが消える(with を抜ける)前に読み切る必要がある。
        data = out.read_bytes()

    if not data:
        raise TTSUnavailable("読み上げの合成結果が空でした")
    return data
