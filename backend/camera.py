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


def max_offset_for(zoom: float) -> float:
    """
    指定倍率で切り抜き中心をずらせる上限(全体比)。

    片側の余白 = (1 - 1/zoom) / 2 = (zoom - 1) / (2 * zoom)。zoom <= 1.0 は 0.0。
    (理由: docs/adr/0007-viewport-crop-calibration.md)
    """
    if zoom <= 1.0:
        return 0.0
    return (zoom - 1.0) / (2.0 * zoom)


def clamp_view(zoom: float, offset_x: float, offset_y: float) -> tuple[float, float, float]:
    """
    ズーム倍率とオフセットを実効範囲に丸め、(zoom, offset_x, offset_y) を返す。

    三つまとめてクランプし直し、-0.0 を 0.0 に正規化する
    (理由: docs/adr/0007-viewport-crop-calibration.md)
    """
    zoom = round(max(1.0, zoom), 4)
    limit = max_offset_for(zoom)
    ox = round(max(-limit, min(limit, offset_x)), 4) + 0.0
    oy = round(max(-limit, min(limit, offset_y)), 4) + 0.0
    return zoom, ox, oy


def crop_zoom(frame, zoom: float, offset_x: float, offset_y: float):
    """
    中央クロップでデジタルズームする。offset は全体比の正規化値で中心をずらす。

    設計判断 (リサイズしない理由など): docs/adr/0007-viewport-crop-calibration.md
    """
    # 1.0未満は元々仕様上無効(config と同じクランプ規則)。ここで揃えておくことで
    # zoom=0.0 のような値が来ても int(w / zoom) のゼロ除算を起こさない。
    zoom = max(zoom, 1.0)
    if zoom == 1.0 and offset_x == 0.0 and offset_y == 0.0:
        return frame

    h, w = frame.shape[:2]
    # 切り抜きサイズ。最低1px、かつ元フレームを超えないようクランプ。
    cw = max(1, min(w, int(w / zoom)))
    ch = max(1, min(h, int(h / zoom)))

    # 中心はオフセット(全体比)ぶんずらす
    cx = w / 2 + offset_x * w
    cy = h / 2 + offset_y * h

    x0 = int(cx - cw / 2)
    y0 = int(cy - ch / 2)
    # オフセットを振り切ってもフレーム内に必ず収まるようクランプ
    # (これにより範囲外アクセスや黒帯が出ない)
    x0 = max(0, min(x0, w - cw))
    y0 = max(0, min(y0, h - ch))

    return frame[y0 : y0 + ch, x0 : x0 + cw]


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
        self,
        index: int,
        width: int,
        height: int,
        fps: int,
        flip: str = "none",
        zoom: float = 1.0,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ):
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self._flip_code = flip_code_for(flip)
        # 公開属性として保持: 書き手は単一の呼び出し元のみなのでロック不要 (理由: docs/adr/0006-single-background-pipeline.md)
        self.zoom, self.offset_x, self.offset_y = clamp_view(zoom, offset_x, offset_y)
        self._cap: Optional[cv2.VideoCapture] = None
        self._out_size: Optional[tuple[int, int]] = None  # 直近read()の実サイズ(w,h)

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

        # read() が self._cap を使うため、ウォームアップの前に代入しておく。
        self._cap = cap

        # 1枚読めるまで self.read() でウォームアップする (理由: docs/adr/0007-viewport-crop-calibration.md)
        for _ in range(5):
            ok, _frame = self.read()
            if ok:
                break
            time.sleep(0.05)

    def read(self):
        """(ok, frame) を返す。frame は BGR numpy 配列(向き補正・ズーム/オフセット適用済み)。"""
        if self._cap is None:
            raise RuntimeError("open() を先に呼んでください。")
        ok, frame = self._cap.read()
        if ok and frame is not None:
            # flipを先に、cropを後に適用する (理由: docs/adr/0007-viewport-crop-calibration.md)
            if self._flip_code is not None:
                frame = cv2.flip(frame, self._flip_code)
            frame = crop_zoom(frame, self.zoom, self.offset_x, self.offset_y)
            self._out_size = (frame.shape[1], frame.shape[0])
        return ok, frame

    @property
    def view(self) -> tuple[float, float, float]:
        """現在の (zoom, offset_x, offset_y)。"""
        return self.zoom, self.offset_x, self.offset_y

    def set_view(self, zoom: float, offset_x: float, offset_y: float) -> tuple[float, float, float]:
        """ズーム/オフセットを実効範囲に丸めて適用し、確定値を返す。"""
        self.zoom, self.offset_x, self.offset_y = clamp_view(zoom, offset_x, offset_y)
        return self.view

    @property
    def actual_size(self) -> tuple[int, int]:
        # クロップ後の実サイズを優先する。まだ1枚も読んでいなければ
        # CAP_PROP ベースの値(クロップ前の生サイズ)にフォールバック。
        if self._out_size is not None:
            return self._out_size
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
