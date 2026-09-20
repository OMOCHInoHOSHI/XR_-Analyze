"""
鑑定文の読み上げ音声(WAV)をディスクへ永続化するキャッシュ。

`server.py` のメモリキャッシュ (`_speak_cache`, 32件LRU) はプロセス内だけに
存在し、サーバを再起動すると消える。合成には1件5〜10秒かかるため、ディスクにも
保存しておき、次回起動時や事前合成スクリプト (`backend/precompute_speak.py`)
から再利用できるようにする。

ファイル名は「ラベル + 本文のハッシュ」で決める。鑑定文が再生成されて内容が
変わった場合に、本文とずれた古い音声を誤って再生することを防ぐためで、
本文が変わればハッシュも変わり別ファイル扱いになる。

設計の経緯: docs/adr/0039-speak-persistent-cache.md
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import config

# ラベルをファイル名に使える文字だけに絞る(それ以外は "_" に置換)。
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]")
# ファイル名が長くなりすぎないよう、ラベル部分は先頭40文字までに切る。
_LABEL_MAX_LEN = 40


def _cache_dir() -> Path | None:
    """保存先ディレクトリ。`config.SPEAK_CACHE_DIR` が空なら None(永続化無効)。"""
    raw = config.SPEAK_CACHE_DIR.strip()
    return Path(raw) if raw else None


def path_for(label: str, text: str) -> Path:
    """
    label と本文から保存先パスを決める。

    `SPEAK_CACHE_DIR` が空でも計算自体は行える(事前合成スクリプトの
    スキップ判定などで、永続化が無効な設定値のまま使われることは無い前提だが
    念のため純粋な計算に留めている)。
    """
    safe_label = _UNSAFE_CHARS_RE.sub("_", label)[:_LABEL_MAX_LEN]
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    base = _cache_dir() or Path(".")
    return base / f"{safe_label}-{digest}.wav"


def load(label: str, text: str) -> bytes | None:
    """
    保存済みのWAVを読む。永続化が無効・未保存・読み込みに失敗した場合は None。
    """
    if _cache_dir() is None:
        return None
    path = path_for(label, text)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        print(f"[tts] 読み上げキャッシュを読めませんでした ({path}): {e}")
        return None


def store(label: str, text: str, data: bytes) -> None:
    """
    合成済みWAVを保存する。永続化が無効なら何もしない。

    一時ファイルへ書いてから os.replace でアトミックに差し替える
    (calib_store.save と同じ方針)。失敗はログのみに留め、呼び出し側の
    処理(音声を返す/合成を続ける)を止めない。
    """
    cache_dir = _cache_dir()
    if cache_dir is None:
        return
    path = path_for(label, text)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[tts] 読み上げキャッシュの保存に失敗しました ({path}): {e}")
