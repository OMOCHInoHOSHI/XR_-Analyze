"""
取得→推論を行う背景スレッド。

カメラ読取とYOLO推論をリクエストから切り離し、常に「最新の1フレーム」と
「最新の検出結果」を保持する。WebSocket/MJPEG はこれを参照するだけなので、
クライアントが何個繋がっても推論は1本で済む。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from . import config
from .camera import Camera, select_camera_index
from .detector import Detection, Detector


class Pipeline:
    def __init__(self) -> None:
        index, reason = select_camera_index(
            config.CAM_INDEX_ENV, config.CAM_NAME, config.CAM_INDEX
        )
        self.cam_index = index
        self.cam_reason = reason
        print(f"[camera] {reason}")
        self._camera = Camera(
            index, config.CAM_WIDTH, config.CAM_HEIGHT, config.CAM_FPS,
            config.CAM_FLIP, config.CAM_ZOOM, config.CAM_OFFSET_X, config.CAM_OFFSET_Y,
        )
        self._detector = Detector(
            config.MODEL, config.CONF_THRES, config.IOU_THRES,
            config.IMG_SIZE, config.DEVICE, config.CLASSES,
        )
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None          # 最新の生フレーム(BGR)
        self._detections: list[Detection] = []            # 最新の検出
        self._frame_id: int = 0                            # フレーム連番
        self._fps: float = 0.0                             # 実測の推論fps
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None

    # --- ライフサイクル ---
    def start(self) -> None:
        self._camera.open()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._camera.release()

    # --- 背景ループ ---
    def _loop(self) -> None:
        last = time.time()
        while self._running:
            ok, frame = self._camera.read()
            if not ok or frame is None:
                time.sleep(0.01)
                continue
            try:
                dets = self._detector.detect(frame)
                # 信頼度の高い順に上限件数だけ残す(表示過多と負荷を抑える)
                if config.MAX_DET > 0 and len(dets) > config.MAX_DET:
                    dets.sort(key=lambda d: d.confidence, reverse=True)
                    dets = dets[: config.MAX_DET]
            except Exception as e:  # 推論失敗してもループは止めない
                self._error = f"detect error: {e}"
                dets = []

            now = time.time()
            dt = now - last
            last = now
            with self._lock:
                self._frame = frame
                self._detections = dets
                self._frame_id += 1
                if dt > 0:
                    # 軽い指数移動平均で実測fpsをならす
                    self._fps = 0.8 * self._fps + 0.2 * (1.0 / dt) if self._fps else 1.0 / dt

    # --- ライブ調整 ---
    def view(self) -> dict:
        """現在のズーム/オフセット。"""
        zoom, off_x, off_y = self._camera.view
        return {"zoom": zoom, "offset_x": off_x, "offset_y": off_y}

    def adjust_view(
        self, dzoom: float = 0.0, dx: float = 0.0, dy: float = 0.0, reset: bool = False
    ) -> dict:
        """
        ズーム/オフセットを差分で動かし、クランプ後の確定値を返す。

        書き込むのはリクエストハンドラのみ、読取ループ(_loop)は参照するだけなので
        ロックは持たない。仮に更新の途中で読まれても、そのフレームの切り抜き位置が
        1コマぶん中途半端になるだけで次のフレームから正しくなる。
        """
        if reset:
            zoom, off_x, off_y = 1.0, 0.0, 0.0
        else:
            zoom, off_x, off_y = self._camera.view
            zoom, off_x, off_y = zoom + dzoom, off_x + dx, off_y + dy
        self._camera.set_view(zoom, off_x, off_y)
        return self.view()

    # --- 参照系(スレッドセーフ) ---
    def snapshot(self) -> tuple[int, Optional[np.ndarray], list[Detection]]:
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            return self._frame_id, frame, list(self._detections)

    def detections_payload(self) -> dict:
        fid, _frame, dets = self.snapshot()
        w, h = self._camera.actual_size
        return {
            "frame_id": fid,
            "ts": time.time(),
            "fps": round(self._fps, 1),
            "source_size": {"w": w, "h": h},
            # フロントの HUD 表示用。サーバが持つ値を正とし、クランプ後の実値を返す。
            "view": self.view(),
            "detections": [d.to_dict() for d in dets],
            "error": self._error,
        }
