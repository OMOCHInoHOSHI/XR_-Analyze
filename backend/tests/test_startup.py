"""
起動画面の unit test (docs/adr/0043-startup-selection-screen.md)。

実カメラ・ultralytics には触れない。Startup の状態遷移と、モデル一覧・カメラの
一覧づくり(probe_cameras)・Pipeline に選択が渡ることを、フェイクで確かめる。

実行: .venv/bin/python -m pytest  (Windows: .venv\\Scripts\\python -m pytest)
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from backend import camera as camera_mod
from backend import devices as devices_mod
from backend import models as models_mod
from backend import pipeline as pipeline_mod
from backend import startup


def wait_for(cond, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return False


class FakePipeline:
    def __init__(self, fail_start: bool = False):
        self.fail_start = fail_start
        self.started = False
        self.stopped = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("カメラを開けません")
        self.started = True

    def stop(self):
        self.stopped = True


# --- Startup の状態遷移 ---

def test_start_reaches_running_and_hands_over_pipeline():
    got = []
    made = []

    def factory(cam_index, cam_name, model):
        made.append((cam_index, cam_name, model))
        return FakePipeline()

    s = startup.Startup(factory, got.append)
    assert s.state == startup.SELECTING
    assert s.start(1, "Innomaker", "yolov8n.pt") is True
    assert wait_for(lambda: s.state == startup.RUNNING)
    assert made == [(1, "Innomaker", "yolov8n.pt")]
    assert len(got) == 1 and got[0].started
    assert s.status()["choice"] == {"cam_index": 1, "cam_name": "Innomaker", "model": "yolov8n.pt"}


def test_second_start_is_rejected_while_running():
    s = startup.Startup(lambda *a: FakePipeline(), lambda p: None)
    s.start(0, "", "a.pt")
    assert wait_for(lambda: s.state == startup.RUNNING)
    assert s.start(1, "", "b.pt") is False


def test_model_load_failure_is_error_and_can_retry():
    calls = {"n": 0}

    def factory(cam_index, cam_name, model):
        calls["n"] += 1
        if model == "broken.pt":
            raise FileNotFoundError("broken.pt が見つかりません")
        return FakePipeline()

    s = startup.Startup(factory, lambda p: None)
    s.start(0, "", "broken.pt")
    assert wait_for(lambda: s.state == startup.ERROR)
    assert "broken.pt" in s.status()["message"]
    # 選び直して再挑戦できる
    assert s.start(0, "", "ok.pt") is True
    assert wait_for(lambda: s.state == startup.RUNNING)


def test_camera_open_failure_releases_and_is_error():
    p = FakePipeline(fail_start=True)
    s = startup.Startup(lambda *a: p, lambda _: pytest.fail("稼働扱いにしてはいけない"))
    s.start(3, "", "a.pt")
    assert wait_for(lambda: s.state == startup.ERROR)
    assert p.stopped, "開けなかったときも片付ける"
    assert "index=3" in s.status()["message"]


def test_cancel_during_startup_stops_pipeline():
    gate = threading.Event()
    p = FakePipeline()

    def factory(*a):
        gate.wait(2)
        return p

    s = startup.Startup(factory, lambda _: pytest.fail("終了後に稼働扱いにしてはいけない"))
    s.start(0, "", "a.pt")
    s.cancel()
    gate.set()
    s.join(2)
    assert p.stopped


# --- モデル一覧 ---

def test_list_models_known_first_and_local_marked(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mod, "DATA_DIR", tmp_path)
    (tmp_path / "yolov8n.pt").write_bytes(b"x" * 1000)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "custom.pt").write_bytes(b"x")
    (tmp_path / "mobileclip_blt.pt").write_bytes(b"x")  # 検出モデルではないので出さない

    ms = models_mod.list_models("yoloe-11l-seg-pf.pt")
    names = [m.name for m in ms]
    assert names[: len(models_mod.KNOWN_MODELS)] == [n for n, _ in models_mod.KNOWN_MODELS]
    assert "custom.pt" in names
    assert "mobileclip_blt.pt" not in names
    by = {m.name: m for m in ms}
    assert by["yolov8n.pt"].local and not by["yoloe-11l-seg-pf.pt"].local
    assert by["custom.pt"].path == str(tmp_path / "models" / "custom.pt")


def test_list_models_adds_unknown_current_on_top(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mod, "DATA_DIR", tmp_path)
    ms = models_mod.list_models("yolo11x.pt")
    assert ms[0].name == "yolo11x.pt" and not ms[0].local


def test_resolve_finds_model_in_models_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mod, "DATA_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "custom.pt").write_bytes(b"x")
    assert models_mod.resolve("custom.pt") == str(tmp_path / "models" / "custom.pt")
    # 手元に無ければ名前のまま (ultralytics にダウンロードさせる)
    assert models_mod.resolve("yolov8n.pt") == "yolov8n.pt"


# --- カメラの一覧づくり ---

def _fake_cap(opened: bool, frame):
    released = []
    cap = SimpleNamespace(
        isOpened=lambda: opened,
        read=lambda: (frame is not None, frame),
        set=lambda *a, **k: True,
        release=lambda: released.append(True),
    )
    cap.released = released
    return cap


def test_probe_cameras_uses_names_and_thumbnails(monkeypatch):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    caps = {0: _fake_cap(True, frame), 1: _fake_cap(True, None)}
    monkeypatch.setattr(devices_mod, "list_camera_names", lambda: ["Built-in", "Innomaker"])
    monkeypatch.setattr(
        camera_mod.cv2, "VideoCapture",
        lambda i, *a: caps.get(i, _fake_cap(False, None)),
    )
    found = devices_mod.probe_cameras(6, 1280, 720, "MJPG")
    assert [c.index for c in found] == [0, 1]  # 名前の数 (2台) だけ試す
    assert found[0].name == "Built-in" and found[0].readable
    assert (found[0].width, found[0].height) == (1280, 720)
    assert found[0].thumbnail and found[0].thumbnail[:2] == b"\xff\xd8"  # JPEG
    assert found[1].name == "Innomaker" and not found[1].readable
    assert all(c.released for c in caps.values()), "調べ終えたら放す"


def test_probe_cameras_without_names_scans_up_to_max(monkeypatch):
    tried = []

    def fake_vc(i, *a):
        tried.append(i)
        return _fake_cap(i == 2, np.zeros((4, 4, 3), dtype=np.uint8))

    monkeypatch.setattr(devices_mod, "list_camera_names", lambda: [])
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", fake_vc)
    found = devices_mod.probe_cameras(3, 640, 480)
    assert sorted(set(tried)) == [0, 1, 2]
    assert [c.index for c in found] == [2] and found[0].name == ""


def test_open_capture_sets_fourcc_before_size(monkeypatch):
    order = []
    cap = SimpleNamespace(isOpened=lambda: True, set=lambda prop, v: order.append(prop))
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", lambda *a: cap)
    assert camera_mod.open_capture(0, 1280, 720, 30, "MJPG") is cap
    cv2 = camera_mod.cv2
    assert order.index(cv2.CAP_PROP_FOURCC) < order.index(cv2.CAP_PROP_FRAME_WIDTH)


# --- Pipeline に選択が渡る ---

def test_pipeline_uses_selected_camera_and_model(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pipeline_mod, "Camera",
        lambda index, *a, **k: seen.setdefault("cam", (index, k.get("fourcc"))) and SimpleNamespace(),
    )
    monkeypatch.setattr(
        pipeline_mod, "Detector",
        lambda model, *a, **k: seen.setdefault("model", model) and SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline_mod, "DetectionStabilizer", lambda **k: SimpleNamespace())
    monkeypatch.setattr(
        pipeline_mod, "select_camera_index",
        lambda *a: pytest.fail("選択済みなら環境変数で選び直さない"),
    )
    monkeypatch.setattr(pipeline_mod.models, "resolve", lambda name: f"/abs/{name}")
    p = pipeline_mod.Pipeline(2, "Innomaker", "yolov8n.pt")
    assert seen["cam"][0] == 2
    assert seen["model"] == "/abs/yolov8n.pt"
    assert p.model == "yolov8n.pt"
    # 名前が分かっていれば再接続は名前で追従する (index 固定にしない)
    assert p._reconnect_explicit is None and p._reconnect_name == "Innomaker"

    p2 = pipeline_mod.Pipeline(4, "", "yolov8n.pt")
    assert p2._reconnect_explicit == 4 and p2._reconnect_name == ""
