"""
音声コマンドの unit test (docs/adr/0045-voice-commands.md)。

実マイク・Vosk には触れない。聞き取り結果の解釈、音量の計算、モデルの取得、
聞き手スレッドの状態遷移、/voice・/shutdown を、フェイクで確かめる。

実行: .venv/bin/python -m pytest  (Windows: .venv\\Scripts\\python -m pytest)
"""
from __future__ import annotations

import json
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server
from backend import voice


def wait_for(cond, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# --- 聞き取り結果の解釈 ---

@pytest.mark.parametrize(
    "text, expected",
    [
        ("モード 変更", ("mode", "モード変更")),
        ("単体 鑑定", ("single", "単体鑑定")),
        ("いち", ("single", "いち")),
        ("広域 鑑定", ("multi", "広域鑑定")),
        ("に", ("multi", "に")),
        ("鑑定", ("appraise", "鑑定")),
        ("とじる", ("close", "とじる")),
        ("終了", ("quit", "終了")),
        ("  終了 ", ("quit", "終了")),
    ],
)
def test_parse_accepts_exact_commands(text, expected):
    assert voice.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[unk]",
        # 読み上げを拾ったときの形。文の途中のコマンド語は受け付けない
        "[unk] [unk] 鑑定 [unk]",
        "鑑定 [unk]",
        "鑑定 終了",
        "モード",
    ],
)
def test_parse_rejects_anything_but_one_command(text):
    assert voice.parse(text) is None


def test_grammar_lists_every_phrase_and_unk():
    assert set(voice.GRAMMAR) == set(voice.PHRASES) | {voice.UNK}


# --- 音量 ---

def pcm(amplitude: int, n: int = 533) -> bytes:
    return (np.ones(n, dtype=np.int16) * amplitude).tobytes()


def test_level_silence_is_zero():
    assert voice.level_of(pcm(0)) == 0.0
    assert voice.level_of(b"") == 0.0


def test_level_is_clamped_and_monotonic():
    quiet = voice.level_of(pcm(30))      # 約 -61 dBFS: 床より下
    mid = voice.level_of(pcm(1000))      # 約 -30 dBFS
    loud = voice.level_of(pcm(32767))    # 0 dBFS: 天井より上
    assert quiet == 0.0
    assert 0.0 < mid < 1.0
    assert loud == 1.0


# --- モデルの取得 ---

def make_model_zip(path, nested: bool = True):
    prefix = "vosk-model-test/" if nested else ""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(prefix + "am/final.mdl", b"x")
        z.writestr(prefix + "conf/model.conf", b"x")
        z.writestr(prefix + "graph/words.txt", b"x")


@pytest.mark.parametrize("nested", [True, False])
def test_ensure_model_downloads_and_extracts(tmp_path, nested):
    src = tmp_path / "m.zip"
    make_model_zip(src, nested)
    dest = tmp_path / "data" / "vosk-model"
    assert voice.ensure_model(dest, src.as_uri())
    assert voice.model_ready(dest)
    # 一時フォルダは片付いている (DATA_DIR に余計なものを残さない)
    assert sorted(p.name for p in dest.parent.iterdir()) == ["vosk-model"]


def test_ensure_model_keeps_existing_model_without_download(tmp_path):
    dest = tmp_path / "vosk-model"
    (dest / "am").mkdir(parents=True)
    (dest / "am" / "final.mdl").write_bytes(b"x")
    (dest / "conf").mkdir()
    assert voice.ensure_model(dest, "http://invalid.invalid/never-fetched.zip")


def test_ensure_model_without_url_or_on_failure_returns_false(tmp_path):
    dest = tmp_path / "vosk-model"
    assert not voice.ensure_model(dest, "")
    assert not voice.ensure_model(dest, (tmp_path / "missing.zip").as_uri())
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", b"not a model")
    assert not voice.ensure_model(dest, bad.as_uri())
    assert not dest.exists()


# --- 聞き手スレッド ---

class FakeRecognizer:
    """AcceptWaveform に渡された塊を、あらかじめ決めた結果の文字列として返す。"""

    def __init__(self, results: dict[bytes, str]):
        self.results = results
        self.last = ""

    def AcceptWaveform(self, data: bytes) -> bool:
        if data in self.results:
            self.last = self.results[data]
            return True
        return False

    def Result(self) -> str:
        return json.dumps({"text": self.last}, ensure_ascii=False)

    def PartialResult(self) -> str:
        return json.dumps({"partial": ""})


class SlowEndpointRecognizer:
    """Vosk が区切らず、途中結果だけが残る状態の代わり。FinalResult で確定する。"""

    def __init__(self, partial: str):
        self.partial = partial
        self.finals = 0

    def AcceptWaveform(self, data: bytes) -> bool:
        return False

    def PartialResult(self) -> str:
        return json.dumps({"partial": self.partial}, ensure_ascii=False)

    def FinalResult(self) -> str:
        self.finals += 1
        text, self.partial = self.partial, ""
        return json.dumps({"text": text}, ensure_ascii=False)


def test_listener_settles_a_partial_that_stops_changing():
    v = voice.VoiceListener(Path("."))
    rec = SlowEndpointRecognizer("終了")
    v.feed(rec, bytes(2), now=0.0)
    v.feed(rec, bytes(2), now=voice.SETTLE_SEC - 0.1)
    assert v.seq == 0                       # まだ話している途中かもしれない
    v.feed(rec, bytes(2), now=voice.SETTLE_SEC + 0.01)
    assert [(c, w) for _, c, w in v.events(0)] == [("quit", "終了")]
    v.feed(rec, bytes(2), now=5.0)          # 空の途中結果では確定させない
    assert rec.finals == 1


def test_settled_partial_with_extra_words_is_still_rejected():
    v = voice.VoiceListener(Path("."))
    rec = SlowEndpointRecognizer("[unk] [unk] 終了")
    v.feed(rec, bytes(2), now=0.0)
    v.feed(rec, bytes(2), now=1.0)
    assert rec.finals == 1 and v.seq == 0


class FakeStream:
    def __init__(self):
        self.active = True
        self.closed = False

    def close(self):
        self.closed = True


def test_listener_emits_only_exact_commands():
    feeds: list = []

    def open_audio(device, on_audio):
        feeds.append(on_audio)
        return FakeStream(), 16000

    rec = FakeRecognizer({b"a": "モード 変更", b"b": "[unk] 鑑定 [unk]", b"c": "に"})
    v = voice.VoiceListener(Path("."), open_audio=open_audio,
                            make_recognizer=lambda rate: rec)
    v.start()
    try:
        assert wait_for(lambda: v.state == voice.LISTENING)
        for chunk in (b"a", b"b", b"c"):
            feeds[0](chunk)
        assert wait_for(lambda: v.seq == 2)
        assert [(c, w) for _, c, w in v.events(0)] == [("mode", "モード変更"), ("multi", "に")]
        assert [c for _, c, _ in v.events(1)] == ["multi"]
    finally:
        v.stop()


def test_listener_reports_no_mic_when_it_cannot_open(monkeypatch):
    monkeypatch.setattr(voice, "MIC_RETRY_SEC", 0.01)

    def open_audio(device, on_audio):
        raise OSError("no input device")

    v = voice.VoiceListener(Path("."), open_audio=open_audio,
                            make_recognizer=lambda rate: FakeRecognizer({}))
    v.start()
    try:
        assert wait_for(lambda: v.state == voice.NO_MIC)
    finally:
        v.stop()


def test_listener_reopens_when_the_mic_goes_away(monkeypatch):
    monkeypatch.setattr(voice, "MIC_RETRY_SEC", 0.01)
    streams: list[FakeStream] = []

    def open_audio(device, on_audio):
        s = FakeStream()
        streams.append(s)
        return s, 16000

    v = voice.VoiceListener(Path("."), open_audio=open_audio,
                            make_recognizer=lambda rate: FakeRecognizer({}))
    v.start()
    try:
        assert wait_for(lambda: len(streams) == 1 and v.state == voice.LISTENING)
        streams[0].active = False      # 抜かれた
        assert wait_for(lambda: len(streams) >= 2, timeout=5)
        assert streams[0].closed
    finally:
        v.stop()


def test_listener_reports_no_model_without_vosk_model(tmp_path):
    # make_recognizer を渡さない = 本物の経路。モデルが無く URL も無いので NO_MODEL
    # (vosk が入っていない環境でも同じく NO_MODEL になる)。
    v = voice.VoiceListener(tmp_path / "none", "", open_audio=lambda d, cb: (FakeStream(), 16000))
    v.start()
    assert wait_for(lambda: v.state == voice.NO_MODEL)


def test_disabled_listener_is_off():
    assert voice.disabled().state == voice.OFF


def test_find_input_device_without_name_is_default():
    assert voice.find_input_device("") is None


# --- /voice と /shutdown ---

class StaticListener:
    """状態と音量を固定で返し、events は外から積める聞き手の代わり。"""

    def __init__(self):
        self.state = voice.LISTENING
        self.level = 0.25
        self.seq = 0
        self._events: list = []
        self._lock = threading.Lock()

    def events(self, after):
        with self._lock:
            return [e for e in self._events if e[0] > after]

    def emit(self, cmd, word):
        with self._lock:
            self.seq += 1
            self._events.append((self.seq, cmd, word))


@pytest.fixture
def fresh_server(monkeypatch):
    listener = StaticListener()
    monkeypatch.setattr(server, "_voice", listener)
    monkeypatch.setattr(server, "_shutting_down", threading.Event())
    monkeypatch.setattr(server, "_voice_conns", set())
    return listener


def recv_until(ws, cond, limit: int = 200):
    for _ in range(limit):
        msg = ws.receive_json()
        if cond(msg):
            return msg
    raise AssertionError("expected message did not arrive")


def test_voice_ws_sends_level_and_new_commands(fresh_server):
    client = TestClient(server.app)
    with client.websocket_connect("/voice") as ws:
        first = ws.receive_json()
        assert first == {"state": voice.LISTENING, "level": 0.25}
        fresh_server.emit("appraise", "鑑定")
        got = recv_until(ws, lambda m: "cmd" in m)
        assert got["cmd"] == "appraise" and got["word"] == "鑑定"


def test_voice_ws_does_not_replay_old_commands(fresh_server):
    fresh_server.emit("quit", "終了")   # つなぐ前に聞き取った言葉
    client = TestClient(server.app)
    with client.websocket_connect("/voice") as ws:
        for _ in range(5):
            assert "cmd" not in ws.receive_json()


def test_voice_ws_sends_commands_only_to_the_newest_tab(fresh_server):
    client = TestClient(server.app)
    with client.websocket_connect("/voice") as old:
        old.receive_json()
        with client.websocket_connect("/voice") as new:
            new.receive_json()
            recv_until(old, lambda m: m["state"] == server.VOICE_ELSEWHERE)
            fresh_server.emit("mode", "モード変更")
            assert recv_until(new, lambda m: "cmd" in m)["cmd"] == "mode"
            for _ in range(5):
                m = old.receive_json()
                assert "cmd" not in m and m["state"] == server.VOICE_ELSEWHERE


def test_shutdown_stops_the_server(fresh_server, monkeypatch):
    fake = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(server, "_uvicorn_server", fake)
    r = TestClient(server.app).post("/shutdown")
    assert r.status_code == 200
    assert server._shutting_down.is_set()
    assert fake.should_exit is True


def test_shutdown_refuses_other_machines(fresh_server, monkeypatch):
    fake = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(server, "_uvicorn_server", fake)
    r = TestClient(server.app, client=("192.168.1.20", 50000)).post("/shutdown")
    assert r.status_code == 403
    assert not server._shutting_down.is_set()
    assert fake.should_exit is False
