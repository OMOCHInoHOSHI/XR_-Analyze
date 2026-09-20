"""
読み上げ音声の永続キャッシュ (docs/adr/0039-speak-persistent-cache.md) の unit test。

Ollama / VOICEVOX には接続しない。`tts.synthesize` は monkeypatch で差し替え、
WAV は `wave` モジュールで作った無音の短いバイト列を使う。

`backend.server` の import はカメラ・YOLO を掴まない(Pipeline は lifespan の中で
初めて構築される)ため、TestClient は使わず `speak()` などの関数を直接呼ぶ。

実行: .venv/bin/python -m pytest
"""
from __future__ import annotations

import wave
from io import BytesIO

import pytest

from backend import config as config_mod
from backend import server as server_mod
from backend import speak_cache, tts


def make_wav(nframes: int = 80, framerate: int = 8000) -> bytes:
    """無音の短いWAVバイト列を作る(テスト用)。"""
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00\x00" * nframes)
    return buf.getvalue()


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """SPEAK_CACHE_DIR をテスト用の一時ディレクトリに差し替える。"""
    d = tmp_path / "speak_cache"
    monkeypatch.setattr(config_mod, "SPEAK_CACHE_DIR", str(d))
    return d


# --- path_for: サニタイズと本文ハッシュ ---

def test_path_for_sanitizes_unsafe_chars(cache_dir):
    path = speak_cache.path_for("a/b c!!", "本文")
    assert path.parent == cache_dir
    assert path.name.startswith("a_b_c__-")
    assert path.suffix == ".wav"


def test_path_for_truncates_long_label(cache_dir):
    label = "x" * 100
    path = speak_cache.path_for(label, "本文")
    stem = path.name[: -len(".wav")]
    label_part, _, digest = stem.rpartition("-")
    assert len(label_part) == 40
    assert len(digest) == 10


def test_path_for_differs_when_text_differs(cache_dir):
    # 同じラベルでも本文が違えば別ファイル(鑑定文が再生成されてもズレた
    # 音声を当てないため。docs/adr/0039-speak-persistent-cache.md)
    p1 = speak_cache.path_for("cup", "本文A")
    p2 = speak_cache.path_for("cup", "本文B")
    assert p1 != p2


# --- store / load 往復 ---

def test_store_then_load_roundtrip(cache_dir):
    data = make_wav()
    speak_cache.store("cup", "本文", data)
    assert speak_cache.load("cup", "本文") == data


def test_load_returns_none_when_missing(cache_dir):
    assert speak_cache.load("cup", "存在しない本文") is None


def test_disabled_when_dir_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "SPEAK_CACHE_DIR", "")
    speak_cache.store("cup", "本文", make_wav())
    assert speak_cache.load("cup", "本文") is None
    # 無効時はディレクトリすら作らない(完全な no-op であることの傍証)
    assert not (tmp_path / "speak_cache").exists()


# --- /speak (server.py) との連携 ---

@pytest.fixture
def clean_speak_state(cache_dir, monkeypatch):
    """server.py のメモリ状態をテストごとに空にする。"""
    server_mod._speak_cache.clear()
    server_mod._speak_jobs.clear()
    server_mod._speak_parts.clear()
    server_mod._speak_ok.clear()
    yield
    server_mod._speak_cache.clear()
    server_mod._speak_jobs.clear()
    server_mod._speak_parts.clear()
    server_mod._speak_ok.clear()


def test_speak_memory_miss_disk_hit_skips_synthesis(clean_speak_state, monkeypatch):
    data = make_wav()
    speak_cache.store("cup", "本文", data)

    def fail_synthesize(text):
        raise AssertionError("ディスクヒットしたのに synthesize が呼ばれた")

    monkeypatch.setattr(tts, "synthesize", fail_synthesize)

    req = server_mod.SpeakRequest(label="cup", text="本文")
    resp = server_mod.speak(req)

    assert resp.body == data
    # メモリLRUにも載る(次回はディスクを読まずに返せる)
    assert server_mod._speak_cache["cup"] == data


def test_speak_fallback_synthesis_is_stored_to_disk(clean_speak_state, monkeypatch):
    data = make_wav()
    monkeypatch.setattr(tts, "synthesize", lambda text: data)

    req = server_mod.SpeakRequest(label="cup", text="本文")
    resp = server_mod.speak(req)

    assert resp.body == data
    assert speak_cache.load("cup", "本文") == data


def test_prefetch_finalize_stores_result_to_disk(clean_speak_state, monkeypatch):
    part = make_wav()
    monkeypatch.setattr(tts, "synthesize", lambda text: part)

    label = "cup"
    text = "これは鑑定文です。"
    server_mod._enqueue_speak_sentence(label, text)
    server_mod._finalize_speak_job(label, text)

    event = server_mod._speak_jobs[label]
    assert event.wait(timeout=5.0), "先読みワーカーが完了しなかった"
    assert label in server_mod._speak_cache
    assert speak_cache.load(label, text) is not None
