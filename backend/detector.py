"""
YOLO による物体検出。

出力はクライアント非依存(Web / Unity / Android のどれでも読める)。
座標は 0.0-1.0 の正規化値で返すので、表示側の解像度に依存しない。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

import cv2
import numpy as np
from ultralytics import YOLO


@dataclass
class Detection:
    """1個の検出結果。bbox は正規化(0-1)。原点は左上、x右/y下が正。"""
    label: str          # クラス名 (例: "cup")
    class_id: int       # COCOクラスID
    confidence: float   # 0-1
    x: float            # bbox左上x (正規化)
    y: float            # bbox左上y (正規化)
    w: float            # bbox幅 (正規化)
    h: float            # bbox高さ (正規化)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Detector:
    def __init__(
        self,
        model_path: str,
        conf: float,
        iou: float,
        imgsz: int,
        device: str = "",
        classes: list[str] | None = None,
    ):
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
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

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        """BGRフレームを推論して Detection のリストを返す。"""
        h, w = frame_bgr.shape[:2]
        results = self.model.predict(
            frame_bgr,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        out: list[Detection] = []
        if not results:
            return out

        r = results[0]
        if r.boxes is None:
            return out

        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
            out.append(
                Detection(
                    label=self.names.get(cls_id, str(cls_id)),
                    class_id=cls_id,
                    confidence=round(conf, 4),
                    x=round(x1 / w, 5),
                    y=round(y1 / h, 5),
                    w=round((x2 - x1) / w, 5),
                    h=round((y2 - y1) / h, 5),
                )
            )
        return out

    @staticmethod
    def annotate(frame_bgr: np.ndarray, detections: list[Detection]) -> np.ndarray:
        """デバッグ用に枠とラベルを描き込んだフレームを返す(元フレームは破壊しない)。"""
        img = frame_bgr.copy()
        H, W = img.shape[:2]
        for d in detections:
            x1 = int(d.x * W)
            y1 = int(d.y * H)
            x2 = int((d.x + d.w) * W)
            y2 = int((d.y + d.h) * H)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            text = f"{d.label} {d.confidence:.2f}"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), (0, 255, 0), -1)
            cv2.putText(
                img, text, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )
        return img
