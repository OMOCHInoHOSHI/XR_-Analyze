"""
鑑定文の読み上げ(音声合成)。

macOS 同梱の `say` コマンドに加え、キャラクター性のある声を出せる
VOICEVOX ENGINE にも対応する。合成処理は本モジュールに閉じ、呼び出し側は
`synthesize()` だけを見ればよい形にする。バックエンドの選択は
`config.TTS_BACKEND` で行う。

方式の選定経緯 (VOICEVOX 導入の経緯を含む):
docs/adr/0025-inspector-voice-readout.md
docs/adr/0026-voicevox-character-voice.md
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
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
    if backend == "voicevox":
        return _voicevox(text)
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


def _voicevox(text: str) -> bytes:
    """
    VOICEVOX ENGINE で WAV を合成する。

    依存を増やさないため HTTP クライアントは stdlib の urllib で済ませる
    (`server.py` の `_ollama_explain` と同じ方針)。呼び出しは2段階:
      1. /audio_query で本文から AudioQuery(発話パラメータのJSON)を作る
      2. 1のパラメータを config の値で上書きし、/synthesis に投げて WAV を得る
    """
    base = config.VOICEVOX_URL.rstrip("/")
    speaker = config.VOICEVOX_SPEAKER

    # 1. AudioQuery を作る。本文はクエリ文字列で渡し、ボディは空でよい。
    query_url = (
        f"{base}/audio_query?text={urllib.parse.quote(text)}&speaker={speaker}"
    )
    query_req = urllib.request.Request(query_url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(query_req, timeout=config.TTS_TIMEOUT_SEC) as resp:
            audio_query = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        # HTTPError(接続先の4xx/5xx)は URLError の派生なので、ここでまとめて受ける
        raise TTSUnavailable(
            "音声合成エンジン (VOICEVOX) に接続できません。起動を確認してください"
        ) from e

    # 2. 話速・音高・抑揚を config の値で上書きしてから合成する。
    audio_query["speedScale"] = config.VOICEVOX_SPEED
    audio_query["pitchScale"] = config.VOICEVOX_PITCH
    audio_query["intonationScale"] = config.VOICEVOX_INTONATION

    synth_url = f"{base}/synthesis?speaker={speaker}"
    synth_req = urllib.request.Request(
        synth_url,
        data=json.dumps(audio_query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(synth_req, timeout=config.TTS_TIMEOUT_SEC) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise TTSUnavailable(
            "音声合成エンジン (VOICEVOX) に接続できません。起動を確認してください"
        ) from e

    if not data:
        raise TTSUnavailable("読み上げの合成結果が空でした")
    return data


def concat_wav(parts: list[bytes]) -> bytes:
    """
    複数のWAVバイト列を1本に結合する。

    鑑定文を文単位で先読み合成した結果 (`server.py` の先読みジョブ) を
    1つのWAVにまとめるために使う。先頭パートのフォーマット
    (チャンネル数・サンプル幅・サンプリングレート)を採用し、以降のパートは
    フレームだけを追記する。VOICEVOXは話者が同じなら揃うはずだが、念のため
    フォーマットが食い違うパートは捨ててログに残す。
    """
    if not parts:
        raise TTSUnavailable("結合するWAVがありません")
    if len(parts) == 1:
        return parts[0]

    with wave.open(io.BytesIO(parts[0]), "rb") as first:
        nchannels = first.getnchannels()
        sampwidth = first.getsampwidth()
        framerate = first.getframerate()
        frames = [first.readframes(first.getnframes())]

    for i, part in enumerate(parts[1:], start=2):
        with wave.open(io.BytesIO(part), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (
                nchannels, sampwidth, framerate,
            ):
                print(f"[tts] concat_wav: {i}番目の断片はフォーマットが異なるため捨てます")
                continue
            frames.append(w.readframes(w.getnframes()))

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(b"".join(frames))
    return out.getvalue()
