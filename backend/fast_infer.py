"""
ultralytics の predict() を経由しない高速推論経路。

この層が必要な理由と省いている処理: docs/adr/0016-mask-free-fast-inference.md
出力(クラス・信頼度・座標)は predict() と一致する。一致しない可能性のある
モデル構成では build() が None を返し、呼び出し側が predict() に戻す。
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import torch
import torchvision

# 前処理のパディング単位。YOLO 系の最大ストライド。
_STRIDE = 32
# レターボックスの余白色(ultralytics の LetterBox と同じ)。
_PAD_VALUE = 114


def letterbox(img: np.ndarray, imgsz: int) -> tuple[np.ndarray, float, int, int]:
    """
    アスペクト比を保ったまま imgsz に収め、ストライドの倍数まで余白を足す。

    ultralytics の LetterBox(auto=True, center=True, scaleup=True) と同じ結果を返す
    (端数の丸め方まで合わせてある。ずらすと bbox が数px ずれる)。

    戻り値: (パディング済みBGR, 縮小率, 左余白px, 上余白px)
    """
    h, w = img.shape[:2]
    gain = min(imgsz / h, imgsz / w)
    nw, nh = round(w * gain), round(h * gain)
    # 余白はストライドの倍数に切り詰める(= 正方形に埋めず矩形推論する)
    dw = ((imgsz - nw) % _STRIDE) / 2
    dh = ((imgsz - nh) % _STRIDE) / 2
    left, right = round(dw - 0.1), round(dw + 0.1)
    top, bottom = round(dh - 0.1), round(dh + 0.1)

    if (w, h) != (nw, nh):
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    if top or bottom or left or right:
        img = cv2.copyMakeBorder(
            img, top, bottom, left, right,
            cv2.BORDER_CONSTANT, value=(_PAD_VALUE, _PAD_VALUE, _PAD_VALUE),
        )
    return img, gain, left, top


class _EmptyProto(torch.nn.Module):
    """proto(マスク原型生成)の差し替え。マスクを使わないので空を返すだけ。"""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.empty(0, device=x.device, dtype=x.dtype)


class _ZeroMaskCoef(torch.nn.Module):
    """
    cv5(マスク係数conv)の差し替え。

    後段が形だけ要求する(出力テンソルに nm 行として連結される)ので、
    同じ形のゼロを返す。連結先は bbox/クラスより後ろの行なので読まれない。
    """

    def __init__(self, nm: int) -> None:
        super().__init__()
        self.nm = nm

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.zeros(
            x.shape[0], self.nm, x.shape[2], x.shape[3], device=x.device, dtype=x.dtype
        )


def _slice_vocab(head, keep: list[int]) -> bool:
    """
    語彙ヘッドを keep のクラスだけに切り詰める。成功したら True。

    プロンプトフリーのモデルは、クラス名の埋め込みが `lrpc[i].vocab` の重みの
    「行」として焼き込まれている。行を抜き出すだけなので、残したクラスのスコアは
    絞り込み前と完全に同じになる。同時に行列積と出力テンソルが小さくなる
    (理由: docs/adr/0018-vocabulary-restriction.md)
    """
    lrpc = getattr(head, "lrpc", None)
    if lrpc is None:
        return False

    index = torch.tensor(keep, dtype=torch.long)
    for sub in lrpc:
        vocab = sub.vocab
        weight, bias = vocab.weight, vocab.bias
        if bias is None:
            return False
        with torch.no_grad():
            picked_w = weight.detach()[index.to(weight.device)].clone()
            picked_b = bias.detach()[index.to(bias.device)].clone()
        if isinstance(vocab, torch.nn.Linear):
            new = torch.nn.Linear(vocab.in_features, len(keep), bias=True)
        elif isinstance(vocab, torch.nn.Conv2d):
            new = torch.nn.Conv2d(vocab.in_channels, len(keep), kernel_size=1, bias=True)
        else:
            return False
        with torch.no_grad():
            new.weight.copy_(picked_w)
            new.bias.copy_(picked_b)
        sub.vocab = new.to(weight.device).to(weight.dtype)
    return True


def _resolve_device(device: str) -> torch.device:
    """空文字なら利用可能な最速デバイスを選ぶ。config._auto_device と同じ優先順位。"""
    if device:
        return torch.device(device)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class FastInferencer:
    """レターボックス → forward → NMS だけを行う。マスクも Results も作らない。"""

    def __init__(
        self,
        net: torch.nn.Module,
        device: torch.device,
        imgsz: int,
        conf: float,
        iou: float,
        max_det: int,
        num_masks: int,
        dedup_iou: float = 0.0,
        class_ids: torch.Tensor | None = None,
        select_rows: bool = False,
    ) -> None:
        # 語彙を絞ったときの「絞り込み後の番号 -> 元の class_id」対応表。
        # None なら絞っていない(番号がそのまま class_id)。
        self._class_ids = class_ids
        # ヘッドを切り詰められなかったモデル向け。後処理でスコアの行を選ぶ。
        # 結果は切り詰めた場合と同じで、速くならないだけ。
        self._select_rows = select_rows
        self._net = net
        self._device = device
        self._imgsz = imgsz
        self._conf = conf
        self._iou = iou
        self._max_det = max_det
        self._num_masks = num_masks
        # 同一物体に別ラベルの枠が重なるのを防ぐ閾値。0 以下で無効。
        self._dedup_iou = dedup_iou
        # NMS へ渡す候補の上限。上位 max_det 件しか使わないので、
        # スコア上位だけ残しても結果は変わらない。無制限指定時は ultralytics と同じ値。
        self._max_candidates = max(300, max_det * 20) if max_det > 0 else 30000

    def infer(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        BGRフレームを推論する。

        戻り値: (bbox xyxy[元画像ピクセル], 信頼度, クラスID) の3つの ndarray。
        """
        h, w = frame_bgr.shape[:2]
        padded, gain, pad_x, pad_y = letterbox(frame_bgr, self._imgsz)
        # BGR->RGB, HWC->CHW, 0-1 正規化 (ultralytics の preprocess と同じ)
        chw = np.ascontiguousarray(padded[:, :, ::-1].transpose(2, 0, 1))

        # 後処理の in-place 演算まで含めて inference_mode の中で完結させる
        with torch.inference_mode():
            im = torch.from_numpy(chw).to(self._device).float().div_(255.0).unsqueeze_(0)
            out = self._net(im)
            pred = out[0]
            if isinstance(pred, (tuple, list)):  # セグメンテーションヘッドは (推論結果, proto)
                pred = pred[0]
            return self._postprocess(pred, gain, pad_x, pad_y, w, h)

    def _postprocess(
        self, pred: torch.Tensor, gain: float, pad_x: int, pad_y: int, w: int, h: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        # pred: (1, 4 + nc + nm, アンカー数)。末尾 nm 行はマスク係数なので読まない。
        p = pred[0]
        nc = p.shape[0] - 4 - self._num_masks
        scores = p[4 : 4 + nc]
        if self._select_rows and self._class_ids is not None:
            scores = scores[self._class_ids.to(scores.device)]
        # クラス次元の最大値だけを見る(ultralytics も multi_label=False で同じ)
        conf, cls = scores.max(0)
        idx = (conf > self._conf).nonzero(as_tuple=True)[0]
        empty = (np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, np.int64))
        if idx.numel() == 0:
            return empty

        conf, cls = conf[idx], cls[idx]
        xywh = p[:4, idx].T
        if conf.numel() > self._max_candidates:
            top = conf.topk(self._max_candidates).indices
            conf, cls, xywh = conf[top], cls[top], xywh[top]

        center, size = xywh[:, :2], xywh[:, 2:]
        boxes = torch.cat((center - size / 2, center + size / 2), 1)
        # ここから先は数百件規模。GPU カーネルの起動待ちより CPU の方が速い。
        boxes, conf, cls = boxes.cpu(), conf.cpu(), cls.cpu()

        kept = torchvision.ops.batched_nms(boxes, conf, cls, self._iou)
        if 0.0 < self._dedup_iou < 1.0 and kept.numel() > 1:
            # クラスをまたいだ重なりを潰す。ほぼ同じ枠に別ラベルが並ぶのを防ぐ
            # (理由: docs/adr/0016-mask-free-fast-inference.md)
            sub = torchvision.ops.nms(boxes[kept], conf[kept], self._dedup_iou)
            kept = kept[sub]
        if self._max_det > 0:
            # batched_nms はスコア降順で返すので、先頭 N 件 = 信頼度上位 N 件
            kept = kept[: self._max_det]
        if kept.numel() == 0:
            return empty
        boxes, conf, cls = boxes[kept], conf[kept], cls[kept]
        if self._class_ids is not None:
            cls = self._class_ids.to(cls.device)[cls]  # 元の class_id へ戻す

        # レターボックスを打ち消して元画像の座標へ戻す
        boxes[:, 0::2] -= pad_x
        boxes[:, 1::2] -= pad_y
        boxes /= gain
        boxes[:, 0::2].clamp_(0, w)
        boxes[:, 1::2].clamp_(0, h)
        return boxes.numpy(), conf.numpy(), cls.to(torch.int64).numpy()


def build(
    model,
    device: str,
    imgsz: int,
    conf: float,
    iou: float,
    max_det: int,
    dedup_iou: float = 0.0,
    keep_classes: Optional[list[int]] = None,
) -> Optional[FastInferencer]:
    """
    YOLO モデルから高速推論経路を組み立てる。

    出力が predict() と一致すると確認できない構成では None を返す。呼び出し側は
    その場合 predict() を使う (理由: docs/adr/0016-mask-free-fast-inference.md)
    """
    try:
        net = model.model  # YOLO ラッパの中身 (nn.Module)
        head = net.model[-1]

        if getattr(head, "end2end", False):
            # NMS 不要モデルは出力形式が (1, max_det, 6) で別物なので対象外
            return None

        torch_device = _resolve_device(device)
        net = net.to(torch_device).float().eval()
        # Conv+BN の融合。ultralytics も predict 時に同じことをしている。
        if hasattr(net, "fuse"):
            net.fuse(verbose=False)

        # マスク経路の切り離し。seg モデルでも bbox の計算には一切関与しない。
        class_ids = None
        select_rows = False
        if keep_classes:
            class_ids = torch.tensor(keep_classes, dtype=torch.long)
            if not _slice_vocab(head, keep_classes):
                # ヘッドを切り詰められないモデルは、後処理で行を選ぶ。
                # 結果は同じで、行列積が小さくならないぶん速くならない。
                select_rows = True

        num_masks = 0
        if hasattr(head, "proto") and hasattr(head, "nm"):
            num_masks = int(head.nm)
            head.proto = _EmptyProto()
            if getattr(head, "cv5", None) is not None:
                head.cv5 = torch.nn.ModuleList(
                    _ZeroMaskCoef(num_masks) for _ in range(head.nl)
                )

        # 重複排除は、マスク経路を外したことで再現できなくなった ultralytics の
        # 「空マスクの検出を捨てる」フィルタを補うもの。マスクを持たない検出モデルでは
        # 補うものが無いので掛けない (理由: docs/adr/0016-mask-free-fast-inference.md)
        fast = FastInferencer(
            net, torch_device, imgsz, conf, iou, max_det, num_masks,
            dedup_iou if num_masks > 0 else 0.0, class_ids, select_rows,
        )
        # 1枚流して形が想定どおりか確かめる(壊れていればここで例外になる)
        fast.infer(np.zeros((64, 64, 3), np.uint8))
        return fast
    except Exception as e:
        print(f"[detector] 高速推論経路を組めませんでした({e})。predict() を使います。")
        return None
