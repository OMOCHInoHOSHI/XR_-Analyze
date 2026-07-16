"""
クロスプラットフォームのカメラ取得。

UVC(USB Video Class)はOS標準プロトコルなので OpenCV が両OSで扱える。
プラットフォーム差はキャプチャ用バックエンドのフラグだけ:
  - macOS:   cv2.CAP_AVFOUNDATION
  - Windows: cv2.CAP_DSHOW (DirectShow)
  - Linux:   cv2.CAP_V4L2
"""
from __future__ import annotations

import platform
import time
from typing import Optional

import cv2

from .devices import resolve_index_by_name


def select_camera_index(
    explicit_index: Optional[int],
    prefer_name: str,
    fallback_index: int,
) -> tuple[int, str]:
    """
    使うべきカメラ index を決める。優先順位:
      1. explicit_index (CAM_INDEX 明示) が指定されていればそれ
      2. prefer_name (CAM_NAME) に名前一致するカメラ
      3. fallback_index
    戻り値: (index, 選択理由の説明)
    """
    if explicit_index is not None:
        return explicit_index, f"CAM_INDEX={explicit_index} を明示指定"
    if prefer_name:
        idx = resolve_index_by_name(prefer_name)
        if idx is not None:
            return idx, f"名前一致 '{prefer_name}' -> index {idx}"
    return fallback_index, (
        f"名前 '{prefer_name}' を解決できず index {fallback_index} にフォールバック"
    )


# CAM_FLIP の値 -> cv2.flip の flipCode。None は反転しない。
_FLIP_CODES: dict[str, Optional[int]] = {
    "180": -1,   # 上下+左右 = 180°回転
    "v": 0,      # 上下のみ
    "h": 1,      # 左右のみ
    "none": None,
    "": None,
}


def flip_code_for(mode: str) -> Optional[int]:
    """CAM_FLIP の文字列を cv2.flip の flipCode に変換する。未知の値は反転なし。"""
    return _FLIP_CODES.get(mode.strip().lower())


def _backend_for_os() -> int:
    system = platform.system()
    if system == "Darwin":
        return cv2.CAP_AVFOUNDATION
    if system == "Windows":
        return cv2.CAP_DSHOW
    if system == "Linux":
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


class Camera:
    """USBカメラからフレームを読むラッパー。"""

    def __init__(
        self, index: int, width: int, height: int, fps: int, flip: str = "none"
    ):
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self._flip_code = flip_code_for(flip)
        self._cap: Optional[cv2.VideoCapture] = None

    def open(self) -> None:
        backend = _backend_for_os()
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            # バックエンド指定で開けない場合はデフォルトで再試行
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            raise RuntimeError(
                f"カメラ index={self.index} を開けませんでした。"
                " CAM_INDEX を変えるか、他アプリがカメラを使っていないか確認してください。"
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # 遅延を減らすためバッファを最小化(対応していないバックエンドでは無視される)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except cv2.error:
            pass

        # 1枚読めるまで軽くウォームアップ
        for _ in range(5):
            ok, _frame = cap.read()
            if ok:
                break
            time.sleep(0.05)

        self._cap = cap

    def read(self):
        """(ok, frame) を返す。frame は BGR numpy 配列(向き補正済み)。"""
        if self._cap is None:
            raise RuntimeError("open() を先に呼んでください。")
        ok, frame = self._cap.read()
        if ok and frame is not None and self._flip_code is not None:
            frame = cv2.flip(frame, self._flip_code)
        return ok, frame

    @property
    def actual_size(self) -> tuple[int, int]:
        if self._cap is None:
            return (self.width, self.height)
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height
        return (w, h)

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "Camera":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
