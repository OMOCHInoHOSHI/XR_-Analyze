"""
取得→推論を行う背景スレッド。

常に「最新の1フレーム/検出結果」だけを保持し、WebSocket/MJPEG はこれを参照する
(理由: docs/adr/0006-single-background-pipeline.md)

取得と推論はスレッドを分けてある。推論がカメラ待ちで止まらず、常に最新フレームを
推論できる (理由: docs/adr/0017-decoupled-capture-thread.md)
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from . import config
from .camera import Camera, select_camera_index
from .detector import Detection, Detector
from .stabilizer import DetectionStabilizer


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
            max_det=config.MAX_DET, fast=config.FAST_INFER,
            dedup_iou=config.DEDUP_IOU, vocab_policy=config.VOCAB,
        )
        self._stabilizer = DetectionStabilizer(
            enabled=config.STAB_ENABLED,
            appear_conf=config.STAB_APPEAR_CONF,
            lose_conf=config.STAB_LOSE_CONF,
            lose_hold_sec=config.STAB_LOSE_HOLD_SEC,
            label_hold_sec=config.STAB_LABEL_HOLD_SEC,
            alpha=config.STAB_ALPHA,
            iou_threshold=config.STAB_IOU,
        )
        # 取得スレッドが書き、推論スレッドが取り出す「最新の生フレーム」1枚だけの受け渡し口。
        self._raw_lock = threading.Lock()
        self._raw_frame: Optional[np.ndarray] = None
        self._raw_ready = threading.Event()

        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None          # 最新の補正済みフレーム(BGR)
        self._detections: list[Detection] = []            # 最新の検出
        self._frame_id: int = 0                            # フレーム連番
        self._fps: float = 0.0                             # 実測の推論fps
        self._running = False
        self._threads: list[threading.Thread] = []
        self._error: Optional[str] = None

    # --- ライフサイクル ---
    def start(self) -> None:
        self._camera.open()
        self._running = True
        self._threads = [
            threading.Thread(target=self._capture_loop, daemon=True),
            threading.Thread(target=self._infer_loop, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._running = False
        self._raw_ready.set()  # 待機中の推論スレッドを起こして終了させる
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []
        self._camera.release()

    # --- 取得スレッド ---
    def _capture_loop(self) -> None:
        """カメラを回し続け、最新の生フレームだけを保持する。古い分は捨てる。"""
        while self._running:
            ok, frame = self._camera.grab()
            if not ok or frame is None:
                time.sleep(0.01)
                continue
            with self._raw_lock:
                self._raw_frame = frame
                # 取り出し側が同じロックの中で clear() するので、set() もロック内で行う
                self._raw_ready.set()

    def _take_raw_frame(self) -> Optional[np.ndarray]:
        """最新の生フレームを取り出す。無ければ届くまで待つ。"""
        if not self._raw_ready.wait(timeout=1.0):
            return None
        with self._raw_lock:
            frame = self._raw_frame
            self._raw_frame = None
            self._raw_ready.clear()
        return frame

    # --- 推論スレッド ---
    def _infer_loop(self) -> None:
        last = time.time()
        while self._running:
            raw = self._take_raw_frame()
            if raw is None:
                continue
            # 向き補正とズームは、実際に推論するフレームにだけ掛ける
            frame = self._camera.transform(raw)
            try:
                # detect() が信頼度上位 MAX_DET 件までに絞って返す
                dets = self._detector.detect(frame)
                # 時系列フィルタでフリッカー(出現/消失/ラベル/confの揺れ)を抑制
                dets = self._stabilizer.update(dets, time.time())
            except Exception as e:  # 推論失敗してもループは止めない
                self._error = f"detect error: {e}"
                # 例外時も安定化層を通す(空を渡す)。生の空で上書きすると表示が一瞬消える
                dets = self._stabilizer.update([], time.time())

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

        書き込みはリクエストハンドラのみなのでロックを持たない (理由: docs/adr/0006-single-background-pipeline.md)
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
        """フレーム付きスナップショット。MJPEG 配信のようにフレームが要る用途向け。"""
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            return self._frame_id, frame, list(self._detections)

    @property
    def frame_id(self) -> int:
        with self._lock:
            return self._frame_id

    def detections_payload(self) -> dict:
        # 検出JSONにフレーム画素は要らないので、ここではコピーしない
        with self._lock:
            fid = self._frame_id
            dets = [d.to_dict() for d in self._detections]
        w, h = self._camera.actual_size
        return {
            "frame_id": fid,
            "ts": time.time(),
            "fps": round(self._fps, 1),
            "source_size": {"w": w, "h": h},
            # フロントの HUD 表示用。サーバが持つ値を正とし、クランプ後の実値を返す。
            "view": self.view(),
            "detections": dets,
            "error": self._error,
        }
