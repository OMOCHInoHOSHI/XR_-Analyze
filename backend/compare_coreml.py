"""
実機カメラで PyTorch 高速経路と CoreML(ANE) 経路を見比べる検証用スクリプト。

同じフレームを両方に流し、速度と「検出結果がどれだけ変わるか」を並べて出す。
CoreML を採用するかどうかを決めるための使い捨てツールで、サーバ本体からは使わない
(検討の経緯: docs/adr/0016-mask-free-fast-inference.md)

    pip install coremltools                      # 検証用の任意依存(本体では使わない)
    python3 -m backend.compare_coreml            # 既定の設定・200フレーム
    python3 -m backend.compare_coreml 400        # フレーム数を指定

初回は CoreML への書き出し(30秒ほど)が走り、.mlpackage を作る(.gitignore 済み)。

2026-08-27 時点の結論は「CoreML は 1.87 倍速いが、安定化層を通した後の表示
ラベルの入れ替わりが約2倍に増えるため未採用」。CLASSES で語彙を絞った場合など、
前提が変わったときにこのスクリプトで測り直すこと。
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision

from . import config
from .camera import Camera, select_camera_index
from .detector import Detector

_MLPACKAGE = Path(config.MODEL).with_suffix(".mlpackage")


# --- CoreML への書き出し ------------------------------------------------------
def _export_if_needed() -> Path:
    """.mlpackage が無ければ書き出す。既にあればそのまま使う。"""
    if _MLPACKAGE.exists():
        print(f"[coreml] 既存の {_MLPACKAGE} を使います")
        return _MLPACKAGE

    print(f"[coreml] {config.MODEL} を CoreML へ書き出します (30秒ほどかかります)")
    from ultralytics import YOLO
    from ultralytics.nn.modules.head import LRPCHead

    def _exportable(self, cls_feat, loc_feat, conf):
        """`.int()` キャストが CoreML の変換器を通らないので `.to(dtype)` に替えただけの版。"""
        if self.enabled:
            pf_score = self.pf(cls_feat)[0, 0].flatten(0)
            mask = pf_score.sigmoid() > conf
            cls_feat = cls_feat.flatten(2).transpose(-1, -2)
            cls_feat = self.vocab(
                cls_feat[:, mask] if conf else cls_feat * mask.unsqueeze(-1).to(cls_feat.dtype)
            )
            return self.loc(loc_feat), cls_feat.transpose(-1, -2), mask
        cls_feat = self.vocab(cls_feat)
        return (
            self.loc(loc_feat),
            cls_feat.flatten(2),
            torch.ones(cls_feat.shape[2] * cls_feat.shape[3], device=cls_feat.device, dtype=torch.bool),
        )

    LRPCHead.forward = _exportable
    out = YOLO(config.MODEL).export(
        format="coreml", imgsz=config.IMG_SIZE, nms=False, device="cpu"
    )
    print(f"[coreml] 書き出し完了: {out}")
    return Path(out)


# --- CoreML 経路 --------------------------------------------------------------
class CoreMLDetector:
    """
    ANE で forward し、クラス方向の集約は MPS で行う。

    集約を CPU でやると 155MB のテンソルの走査に 140ms 以上かかり、
    PyTorch 経路より遅くなる。ANE と MPS の両方を使って初めて速い。
    """

    def __init__(self, package: Path, names: dict[int, str]) -> None:
        import coremltools as ct
        from PIL import Image

        self._Image = Image
        self._names = names
        self._nc = len(names)
        self._model = ct.models.MLModel(str(package), compute_units=ct.ComputeUnit.CPU_AND_NE)
        spec = self._model.get_spec()
        self._input = spec.description.input[0].name
        # スコアのテンソル(アンカー数が多い方)を選ぶ。もう片方はマスク原型。
        self._output = max(
            (o for o in spec.description.output),
            key=lambda o: int(o.type.multiArrayType.shape[1]),
        ).name
        self._size = spec.description.input[0].type.imageType.width

    def _letterbox(self, img: np.ndarray):
        """CoreML の入力は正方形固定なので、矩形ではなく正方形に詰める。"""
        sz = self._size
        h, w = img.shape[:2]
        gain = min(sz / h, sz / w)
        nw, nh = round(w * gain), round(h * gain)
        dw, dh = (sz - nw) / 2, (sz - nh) / 2
        left, top = round(dw - 0.1), round(dh - 0.1)
        padded = cv2.copyMakeBorder(
            cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR),
            top, round(dh + 0.1), left, round(dw + 0.1),
            cv2.BORDER_CONSTANT, value=(114, 114, 114),
        )
        return padded, gain, left, top

    def detect(self, frame_bgr: np.ndarray) -> list[tuple[str, float, tuple]]:
        h, w = frame_bgr.shape[:2]
        padded, gain, pad_x, pad_y = self._letterbox(frame_bgr)
        pil = self._Image.fromarray(cv2.cvtColor(padded, cv2.COLOR_BGR2RGB))
        raw = self._model.predict({self._input: pil})[self._output]

        p = torch.from_numpy(np.ascontiguousarray(raw)).to("mps")[0]
        conf, cls = p[4 : 4 + self._nc].max(0)
        idx = (conf > config.CONF_THRES).nonzero(as_tuple=True)[0]
        if idx.numel() == 0:
            return []
        conf, cls, xywh = conf[idx], cls[idx], p[:4, idx].T
        if conf.numel() > 300:
            top = conf.topk(300).indices
            conf, cls, xywh = conf[top], cls[top], xywh[top]
        center, size = xywh[:, :2], xywh[:, 2:]
        boxes = torch.cat((center - size / 2, center + size / 2), 1).cpu()
        conf, cls = conf.cpu(), cls.cpu()

        kept = torchvision.ops.batched_nms(boxes, conf, cls, config.IOU_THRES)
        if 0.0 < config.DEDUP_IOU < 1.0 and kept.numel() > 1:
            kept = kept[torchvision.ops.nms(boxes[kept], conf[kept], config.DEDUP_IOU)]
        if config.MAX_DET > 0:
            kept = kept[: config.MAX_DET]
        boxes, conf, cls = boxes[kept], conf[kept], cls[kept]
        boxes[:, 0::2] -= pad_x
        boxes[:, 1::2] -= pad_y
        boxes /= gain
        return [
            (self._names[int(c)], float(s), (float(b[0]) / w, float(b[1]) / h,
                                             float(b[2] - b[0]) / w, float(b[3] - b[1]) / h))
            for b, s, c in zip(boxes, conf, cls)
        ]


# --- 突き合わせ ---------------------------------------------------------------
def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def main() -> None:
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 200

    package = _export_if_needed()

    index, reason = select_camera_index(config.CAM_INDEX_ENV, config.CAM_NAME, config.CAM_INDEX)
    print(f"[camera] {reason}")
    cam = Camera(
        index, config.CAM_WIDTH, config.CAM_HEIGHT, config.CAM_FPS,
        config.CAM_FLIP, config.CAM_ZOOM, config.CAM_OFFSET_X, config.CAM_OFFSET_Y,
    )
    cam.open()

    py = Detector(
        config.MODEL, config.CONF_THRES, config.IOU_THRES, config.IMG_SIZE,
        config.DEVICE, config.CLASSES, max_det=config.MAX_DET,
        fast=True, dedup_iou=config.DEDUP_IOU,
    )
    cm = CoreMLDetector(package, py.names)

    print(f"\nカメラに鑑定したいものを映してください。{frames} フレーム測ります。\n")
    t_py: list[float] = []
    t_cm: list[float] = []
    same_set = 0          # 表示ラベルの集合が一致したフレーム数
    same_top = 0          # 最上位(単体鑑定で出る1件)のラベルが一致したフレーム数
    both_nonempty = 0
    deltas: list[float] = []   # 対応が取れた枠の信頼度の差
    only_py: dict[str, int] = {}
    only_cm: dict[str, int] = {}
    n = 0

    try:
        while n < frames:
            ok, frame = cam.read()
            if not ok or frame is None:
                time.sleep(0.01)
                continue

            t0 = time.perf_counter()
            a = [(d.label, d.confidence, (d.x, d.y, d.w, d.h)) for d in py.detect(frame)]
            torch.mps.synchronize()
            t_py.append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            b = cm.detect(frame)
            torch.mps.synchronize()
            t_cm.append((time.perf_counter() - t0) * 1000)

            n += 1
            la, lb = sorted(x[0] for x in a), sorted(x[0] for x in b)
            if la == lb:
                same_set += 1
            if a and b:
                both_nonempty += 1
                if a[0][0] == b[0][0]:
                    same_top += 1
            for label in set(la) - set(lb):
                only_py[label] = only_py.get(label, 0) + 1
            for label in set(lb) - set(la):
                only_cm[label] = only_cm.get(label, 0) + 1
            # 同じラベルで重なる枠どうしを対応付けて信頼度の差を見る
            for lab, conf_a, box_a in a:
                cands = [s for l, s, bx in b if l == lab and _iou(box_a, bx) > 0.5]
                if cands:
                    deltas.append(abs(conf_a - max(cands)))

            if n % 25 == 0:
                print(f"  {n}/{frames} フレーム...")
    except KeyboardInterrupt:
        print("\n中断しました")
    finally:
        cam.release()

    if not n:
        print("フレームを取得できませんでした")
        return

    mp, mc = statistics.median(t_py), statistics.median(t_cm)
    print(f"\n=== 速度 ({n} フレーム / 中央値) ===")
    print(f"  PyTorch 高速経路 : {mp:6.1f}ms ({1000 / mp:5.2f} fps)")
    print(f"  CoreML(ANE)経路  : {mc:6.1f}ms ({1000 / mc:5.2f} fps)   倍率 {mp / mc:.2f}x")

    print(f"\n=== 検出結果の違い ===")
    print(f"  表示ラベルの集合が一致  : {same_set}/{n} フレーム ({100 * same_set / n:.0f}%)")
    if both_nonempty:
        print(f"  最上位1件のラベルが一致 : {same_top}/{both_nonempty} フレーム "
              f"({100 * same_top / both_nonempty:.0f}%)  ← 単体鑑定で見えるもの")
    if deltas:
        print(f"  信頼度の差 (同じ枠どうし): 中央値 {statistics.median(deltas):.4f} / "
              f"最大 {max(deltas):.4f}")
    if only_py:
        print(f"  PyTorch にだけ出た : {sorted(only_py.items(), key=lambda x: -x[1])[:8]}")
    if only_cm:
        print(f"  CoreML にだけ出た  : {sorted(only_cm.items(), key=lambda x: -x[1])[:8]}")
    if not only_py and not only_cm:
        print("  片方にだけ出た検出はありませんでした")


if __name__ == "__main__":
    main()
