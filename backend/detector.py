"""
YOLO による物体検出。

出力はクライアント非依存、座標は正規化値 (理由: docs/adr/0002-detection-streaming-contract.md)
"""
from __future__ import annotations

import os
import platform
from dataclasses import asdict, dataclass
from typing import Any, Optional

import cv2
import numpy as np
from ultralytics import YOLO

from . import fast_infer
from .labels_ja import to_ja


@dataclass
class Detection:
    """1個の検出結果。bbox は正規化(0-1)。原点は左上、x右/y下が正。"""
    label: str          # 英語クラス名 (例: "cup") クライアント非依存の契約用に保持
    label_ja: str       # 日本語表示名 (例: "カップ") 辞書に無い語は英語のまま
    class_id: int       # COCOクラスID
    confidence: float   # 0-1
    x: float            # bbox左上x (正規化)
    y: float            # bbox左上y (正規化)
    w: float            # bbox幅 (正規化)
    h: float            # bbox高さ (正規化)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- 日本語フォントの探索(MJPEG描画用) ---------------------------------------
# OpenCVのputTextは日本語を描けないため、PILで描画する。日本語対応フォントを探す。
def _jp_font_candidates() -> list[str]:
    env = os.environ.get("JP_FONT", "").strip()
    cands = [env] if env else []
    system = platform.system()
    if system == "Darwin":
        cands += [
            "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    elif system == "Windows":
        cands += [
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
        ]
    else:  # Linux
        cands += [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        ]
    return cands


_FONT_CACHE: dict[int, Any] = {}
_FONT_MISSING = False


def _load_jp_font(size: int):
    """日本語フォントを返す。見つからなければ None(=英語描画にフォールバック)。"""
    global _FONT_MISSING
    if _FONT_MISSING:
        return None
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    try:
        from PIL import ImageFont
    except Exception:
        _FONT_MISSING = True
        return None
    for path in _jp_font_candidates():
        if path and os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[size] = font
                return font
            except Exception:
                continue
    _FONT_MISSING = True  # 一度見つからなければ以後は探さない
    return None


class Detector:
    def __init__(
        self,
        model_path: str,
        conf: float,
        iou: float,
        imgsz: int,
        device: str = "",
        classes: list[str] | None = None,
        max_det: int = 0,
        fast: bool = True,
        dedup_iou: float = 0.0,
    ):
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
        self.max_det = max_det
        self.device = device or None  # 空文字なら ultralytics の自動選択
        self.model = YOLO(model_path)

        # オープン語彙モデル(YOLOE/YOLO-World)でクラス指定がある場合は絞り込む。
        # 通常のYOLO(COCO)モデルは set_classes を持たないので、その場合は無視。
        if classes:
            if hasattr(self.model, "set_classes"):
                try:
                    self.model.set_classes(classes)
                    print(f"[detector] オープン語彙クラスを設定: {classes}")
                except Exception as e:
                    print(
                        f"[detector] set_classes 失敗({e})。"
                        " このモデルはテキスト指定に非対応の可能性があります。"
                    )
            else:
                print(
                    "[detector] このモデルはクラス指定(set_classes)に非対応のため "
                    "CLASSES を無視します。YOLOE/YOLO-World系のモデルを指定してください。"
                )

        self.names: dict[int, str] = self.model.names

        # マスク生成と Results 構築を省いた推論経路 (理由: docs/adr/0016-mask-free-fast-inference.md)
        # 組めない構成では None が返り、predict() 経路にそのまま落ちる。
        self._fast = (
            fast_infer.build(self.model, device, imgsz, conf, iou, max_det, dedup_iou)
            if fast
            else None
        )
        route = "高速(マスク省略)" if self._fast else "ultralytics predict()"
        print(f"[detector] 推論経路: {route}")

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        """BGRフレームを推論して Detection のリストを返す。信頼度の高い順。"""
        h, w = frame_bgr.shape[:2]
        if self._fast is not None:
            boxes, confs, class_ids = self._fast.infer(frame_bgr)
        else:
            boxes, confs, class_ids = self._predict_boxes(frame_bgr)

        return [
            self._to_detection(box, float(conf), int(cls_id), w, h)
            for box, conf, cls_id in zip(boxes, confs, class_ids)
        ]

    def _predict_boxes(self, frame_bgr: np.ndarray):
        """ultralytics の predict() 経路。高速経路を組めなかった場合のフォールバック。"""
        results = self.model.predict(
            frame_bgr,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            device=self.device,
            max_det=self.max_det if self.max_det > 0 else 300,
            verbose=False,
        )
        if not results or results[0].boxes is None:
            return [], [], []
        b = results[0].boxes
        return b.xyxy.cpu().numpy(), b.conf.cpu().numpy(), b.cls.cpu().numpy()

    def _to_detection(self, box, conf: float, cls_id: int, w: int, h: int) -> Detection:
        """xyxy(元画像ピクセル)を正規化 bbox の Detection へ変換する。"""
        x1, y1, x2, y2 = (float(v) for v in box)
        label_en = self.names.get(cls_id, str(cls_id))
        return Detection(
            label=label_en,
            label_ja=to_ja(label_en),
            class_id=cls_id,
            confidence=round(conf, 4),
            x=round(x1 / w, 5),
            y=round(y1 / h, 5),
            w=round((x2 - x1) / w, 5),
            h=round((y2 - y1) / h, 5),
        )

    @staticmethod
    def annotate(frame_bgr: np.ndarray, detections: list[Detection]) -> np.ndarray:
        """
        デバッグ用に枠と日本語ラベルを描き込んだフレームを返す(元は破壊しない)。
        日本語フォントが見つかれば PIL で日本語を大きく描画、無ければ英語(cv2)に
        フォールバックする。
        """
        H, W = frame_bgr.shape[:2]
        # フォントサイズは画面高さに比例(大きめ)。最低24px。
        font_size = max(24, int(H * 0.045))
        font = _load_jp_font(font_size)

        if font is not None:
            return Detector._annotate_pil(frame_bgr, detections, font, font_size)
        return Detector._annotate_cv2(frame_bgr, detections, font_size)

    @staticmethod
    def _annotate_pil(frame_bgr, detections, font, font_size):
        from PIL import Image, ImageDraw

        H, W = frame_bgr.shape[:2]
        img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img)
        box_w = max(2, int(H * 0.004))
        for d in detections:
            x1 = int(d.x * W); y1 = int(d.y * H)
            x2 = int((d.x + d.w) * W); y2 = int((d.y + d.h) * H)
            draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 102), width=box_w)
            text = f"{d.label_ja} {int(d.confidence * 100)}%"
            try:
                l, t, r, b = draw.textbbox((0, 0), text, font=font)
                tw, th = r - l, b - t
            except Exception:
                tw, th = font_size * len(text) // 2, font_size
            ty = max(0, y1 - th - 8)
            draw.rectangle([x1, ty, x1 + tw + 10, ty + th + 8], fill=(0, 255, 102))
            draw.text((x1 + 5, ty + 2), text, font=font, fill=(0, 0, 0))
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    @staticmethod
    def _annotate_cv2(frame_bgr, detections, font_size):
        # 日本語フォントが無い環境向け: 英語ラベルを大きめに描画
        img = frame_bgr.copy()
        H, W = img.shape[:2]
        scale = font_size / 28.0
        thick = max(1, int(scale * 2))
        for d in detections:
            x1 = int(d.x * W); y1 = int(d.y * H)
            x2 = int((d.x + d.w) * W); y2 = int((d.y + d.h) * H)
            cv2.rectangle(img, (x1, y1), (x2, y2), (102, 255, 0), max(2, thick))
            text = f"{d.label} {int(d.confidence * 100)}%"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            cv2.rectangle(img, (x1, y1 - th - 10), (x1 + tw + 8, y1), (102, 255, 0), -1)
            cv2.putText(
                img, text, (x1 + 4, y1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick, cv2.LINE_AA,
            )
        return img
