"""
tts._voicevox の例外変換の unit test。VOICEVOX には接続しない。

接続後に応答が壊れた場合(http.client.HTTPException 系)も TTSUnavailable に
まとめられることを確認する。実測で ENGINE が連続合成中に一度だけ空応答
(BadStatusLine)を返し、事前合成が例外で落ちた事象への回帰テスト。
実行: .venv/bin/python -m pytest
"""
from __future__ import annotations

import http.client
import urllib.request

import pytest

from backend import config as config_mod
from backend import tts


@pytest.fixture(autouse=True)
def voicevox_backend(monkeypatch):
    monkeypatch.setattr(config_mod, "TTS_BACKEND", "voicevox")


@pytest.mark.parametrize(
    "exc",
    [
        http.client.BadStatusLine("POST /audio_query HTTP/1.1"),
        http.client.RemoteDisconnected("Remote end closed connection"),
        http.client.IncompleteRead(b""),
        TimeoutError("timed out"),
    ],
)
def test_broken_response_becomes_tts_unavailable(monkeypatch, exc):
    def broken_urlopen(req, timeout=None):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", broken_urlopen)
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize("本文。")


def test_broken_synthesis_response_becomes_tts_unavailable(monkeypatch):
    """audio_query は通り、synthesis の応答が壊れた場合も同じ扱い。"""
    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"speedScale": 1.0}'

    def urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp()
        raise http.client.BadStatusLine("")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize("本文。")
    assert calls["n"] == 2
