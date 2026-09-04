"""
カメラ自動再接続の unit test (docs/adr/0035-camera-auto-reconnect.md)。

重い依存(実カメラ・ultralytics の推論)には触れない。Pipeline が __init__ で
組み立てる Camera/Detector/DetectionStabilizer とカメラ選択をフェイクに差し替え、
取得ループの状態遷移(通常 → 切断 → 再接続 → 復帰)を検証する。

実行: .venv/bin/python -m pytest
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from backend import camera as camera_mod
from backend import config as config_mod
from backend import pipeline as pipeline_mod
from backend.camera import Camera, reconnect_candidates
from backend.detector import Detection
from backend.pipeline import Pipeline


def wait_for(cond, timeout: float = 5.0, interval: float = 0.005) -> bool:
    """cond() が真になるまで待つ。タイムアウトで False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


def make_det() -> Detection:
    return Detection(
        label="cup", label_ja="カップ", class_id=41,
        confidence=0.9, x=0.1, y=0.1, w=0.2, h=0.2,
    )


class FakeCamera:
    """
    Pipeline 用のフェイクカメラ。

    broken=True の間 grab() が失敗する(= カメラが外れた状態)。
    fail_opens>0 の間 try_open() が失敗する(= 刺さり直してもすぐは開かない)。
    """

    def __init__(self):
        self.index = 0
        self.opened = True
        self.broken = False
        self.fail_opens = 0
        self.releases = 0
        self.reopens = 0
        self.tries: list[int] = []  # try_open に渡った index の履歴

    def grab(self):
        if not self.opened or self.broken:
            return False, None
        return True, np.full((2, 3, 3), 7, dtype=np.uint8)

    def try_open(self) -> bool:
        self.tries.append(self.index)
        if self.fail_opens > 0:
            self.fail_opens -= 1
            return False
        self.opened = True
        self.broken = False
        self.reopens += 1
        return True

    def release(self) -> None:
        self.releases += 1
        self.opened = False

    @property
    def actual_size(self) -> tuple[int, int]:
        return (640, 480)

    def transform(self, frame):
        return frame

    @property
    def view(self) -> tuple[float, float, float]:
        return (1.0, 0.0, 0.0)


@pytest.fixture
def fake_camera(monkeypatch):
    """Pipeline の重い依存をフェイクに差し替える。戻り値は注入した FakeCamera。"""
    cam = FakeCamera()
    monkeypatch.setattr(pipeline_mod, "Camera", lambda *a, **k: cam)
    monkeypatch.setattr(
        pipeline_mod, "Detector",
        lambda *a, **k: SimpleNamespace(detect=lambda frame: [make_det()]),
    )
    monkeypatch.setattr(
        pipeline_mod, "DetectionStabilizer",
        lambda **k: SimpleNamespace(update=lambda dets, ts: dets),
    )
    # 起動時のカメラ選択は実機に触れないよう固定する
    monkeypatch.setattr(pipeline_mod, "select_camera_index", lambda *a, **k: (0, "fake"))
    # 再接続時の名前再解決も実機(AVFoundation)に触れないよう固定する
    monkeypatch.setattr(camera_mod, "resolve_index_by_name", lambda name: 2)
    return cam


def make_pipeline(monkeypatch, **overrides) -> Pipeline:
    for key, value in overrides.items():
        monkeypatch.setattr(config_mod, key, value)
    return Pipeline()


# --- reconnect_candidates (再オープンの候補順) ---

def test_reconnect_candidates_recorded_first_then_name(monkeypatch):
    monkeypatch.setattr(camera_mod, "resolve_index_by_name", lambda name: 2)
    assert reconnect_candidates(0, None, "Innomaker") == [0, 2]


def test_reconnect_candidates_explicit_index_skips_name(monkeypatch):
    # CAM_INDEX が明示されているときは名前再解決をしない(起動時の優先順位を尊重)
    monkeypatch.setattr(camera_mod, "resolve_index_by_name", lambda name: 2)
    assert reconnect_candidates(1, 1, "Innomaker") == [1]


def test_reconnect_candidates_dedupes(monkeypatch):
    monkeypatch.setattr(camera_mod, "resolve_index_by_name", lambda name: 0)
    assert reconnect_candidates(0, None, "Innomaker") == [0]


def test_reconnect_candidates_unresolvable_name(monkeypatch):
    monkeypatch.setattr(camera_mod, "resolve_index_by_name", lambda name: None)
    assert reconnect_candidates(0, None, "Innomaker") == [0]


# --- Camera.try_open (成否を返す再オープン用 API) ---

def test_camera_try_open_false_when_unavailable(monkeypatch):
    closed = SimpleNamespace(isOpened=lambda: False)
    monkeypatch.setattr(
        camera_mod.cv2, "VideoCapture",
        lambda *a, **k: closed,
    )
    cam = Camera(0, 640, 480, 30)
    assert cam.try_open() is False


def test_camera_open_raises_when_unavailable(monkeypatch):
    # 起動時の fail-fast は従来どおり RuntimeError
    closed = SimpleNamespace(isOpened=lambda: False)
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", lambda *a, **k: closed)
    cam = Camera(0, 640, 480, 30)
    with pytest.raises(RuntimeError):
        cam.open()


def test_camera_try_open_success(monkeypatch):
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    cap = SimpleNamespace(
        isOpened=lambda: True,
        read=lambda: (True, frame),
        set=lambda *a, **k: None,
    )
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = Camera(1, 640, 480, 30)
    assert cam.try_open() is True
    assert cam._cap is cap


# --- Pipeline の取得ループ(切断 → 再接続 → 復帰) ---

def test_capture_loop_reconnects_and_resumes(fake_camera, monkeypatch):
    p = make_pipeline(
        monkeypatch, CAM_RECONNECT=True, CAM_FAIL_LIMIT=3, CAM_RETRY_WAIT=0.05,
    )
    p._running = True
    capture = threading.Thread(target=p._capture_loop, daemon=True)
    infer = threading.Thread(target=p._infer_loop, daemon=True)
    capture.start()
    infer.start()
    try:
        # 通常経路: フレームが流れ、検出が出る
        assert wait_for(lambda: p.frame_id > 0), "通常経路でフレームが流れない"
        ok_status = p.detections_payload()["camera_status"]
        assert ok_status == "ok"
        before = p.frame_id

        # --- 切断 ---
        # 切断検知 → プレースホルダー配信 + camera_status が reconnecting へ。
        # try_open が fail_opens 回失敗するので、reconnecting 窓は十分に長い。
        # (broken より先に設定しないと、検知〜再接続が一瞬で完結して観測できない。
        #  9回=奇数にして、名前再解決の index で成功するようにしている)
        fake_camera.fail_opens = 9
        fake_camera.broken = True
        assert wait_for(lambda: p.reconnecting), "切断が検知されない"
        assert wait_for(lambda: p.detections_payload()["camera_status"] == "reconnecting")

        def placeholder_up() -> bool:
            _fid, frame, dets = p.snapshot()
            # 通常フレームは検出つき(フェイク detector が1件返す)なので、
            # 検出が空 & サイズ一致のフレームはプレースホルダー
            return frame is not None and dets == [] and frame.shape[:2] == (480, 640)

        assert wait_for(placeholder_up), "プレースホルダーが配信されていない"

        # --- 再接続 ---
        assert wait_for(lambda: not p.reconnecting), "再接続が完了しない"
        assert fake_camera.reopens == 1
        assert fake_camera.releases >= 1
        # 起動時に記録した index を最優先で試している
        assert fake_camera.tries[0] == 0
        # 名前再解決の index (2) も試し、最終的にそこで開いている
        assert 2 in fake_camera.tries
        assert p.cam_index == 2
        assert p.detections_payload()["camera_status"] == "ok"

        # --- 復帰: 画面と処理が自動で再開する ---
        assert wait_for(
            lambda: p.detections_payload()["detections"], timeout=10.0
        ), "復帰後に処理が再開しない"
        assert p.frame_id > before
    finally:
        p._running = False
        p._raw_ready.set()
        capture.join(timeout=5.0)
        infer.join(timeout=5.0)


def test_infer_loop_does_not_overwrite_while_reconnecting(fake_camera, monkeypatch):
    # 再接続中は推論結果(古い検出)でプレースホルダーを上書きしない
    p = make_pipeline(monkeypatch)
    p._enter_reconnecting()  # reconnecting 状態を直接作る
    p._running = True
    p._publish_raw(np.full((2, 3, 3), 7, dtype=np.uint8))
    infer = threading.Thread(target=p._infer_loop, daemon=True)
    infer.start()
    try:
        # 推論が raw フレームを処理し切るのを十分待つ
        time.sleep(0.3)
        _fid, frame, dets = p.snapshot()
        assert dets == [], "再接続中に推論結果で画面が上書きされた"
        assert p.detections_payload()["camera_status"] == "reconnecting"
    finally:
        p._running = False
        p._raw_ready.set()
        infer.join(timeout=5.0)


def test_capture_loop_stays_down_without_reconnect(fake_camera, monkeypatch):
    # CAM_RECONNECT=0 なら従来どおり再オープンしない(挙動を変えない)
    p = make_pipeline(
        monkeypatch, CAM_RECONNECT=False, CAM_FAIL_LIMIT=3, CAM_RETRY_WAIT=0.05,
    )
    p._running = True
    capture = threading.Thread(target=p._capture_loop, daemon=True)
    infer = threading.Thread(target=p._infer_loop, daemon=True)
    capture.start()
    infer.start()
    try:
        assert wait_for(lambda: p.frame_id > 0)
        fake_camera.broken = True
        time.sleep(0.5)
        assert not p.reconnecting
        assert fake_camera.reopens == 0
    finally:
        p._running = False
        p._raw_ready.set()
        capture.join(timeout=5.0)
        infer.join(timeout=5.0)


def test_placeholder_and_status_without_frames_yet(fake_camera, monkeypatch):
    # 1度もフレームを読む前に切れても、プレースホルダーと status が組める
    p = make_pipeline(
        monkeypatch, CAM_RECONNECT=True, CAM_FAIL_LIMIT=1, CAM_RETRY_WAIT=0.05,
    )
    p._running = True
    capture = threading.Thread(target=p._capture_loop, daemon=True)
    fake_camera.broken = True
    # try_open を失敗させて reconnecting 窓を確保する(開始前に設定)。
    # 奇数回にして、成功するのが名前再解決の index になるようにもする
    fake_camera.fail_opens = 5
    capture.start()
    try:
        assert wait_for(lambda: p.reconnecting), "切断が検知されない"
        payload = p.detections_payload()
        assert payload["camera_status"] == "reconnecting"
        _fid, frame, dets = p.snapshot()
        assert frame is not None and frame.shape[:2] == (480, 640)
        assert dets == []
    finally:
        p._running = False
        p._raw_ready.set()
        capture.join(timeout=5.0)
