"""
検出結果の時系列安定化層。

カメラ・モデル非依存。Detection のリストを受け取り、フリッカー(出現/消失の
ちらつき、ラベル揺れ、conf の揺らぎ)を抑えた Detection リストを返すだけ。
重厚なトラッカーではなく、純粋な時系列フィルタとして実装している
(理由: docs/adr/0012 の意図。ID 恒久保持や軌跡予測は行わない)。
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .detector import Detection

from .labels_ja import to_ja

# ラベルが変わった場合でも同一物体と見なす IoU 閾値。
# 出現/消失ヒステリシスとは別に、ラベル揺れ時の引き継ぎを許容するため緩めに設定。
_LABEL_CHANGE_IOU = 0.5


class _Track:
    """内部状態。1つの安定化対象(= ある物体の時系列)を表す。"""

    def __init__(self, det: Detection, now: float, appear_conf: float) -> None:
        self.label: str = det.label
        self.class_id: int = det.class_id
        self.raw: Detection = det  # replace 用に最新の生 Detection を保持
        self.ema_conf: float = det.confidence
        self.ema_x: float = det.x
        self.ema_y: float = det.y
        self.ema_w: float = det.w
        self.ema_h: float = det.h
        self.active: bool = det.confidence >= appear_conf
        self.last_matched_now: float = now
        self.lost_since: float | None = None
        self.label_candidate: tuple[str, int, float] | None = None
        self.label_candidate_min_conf: float = 0.0


def _iou(a: _Track, b: Detection) -> float:
    """2つの bbox (正規化座標) の IoU を計算する。"""
    ax1, ay1 = a.ema_x, a.ema_y
    ax2, ay2 = a.ema_x + a.ema_w, a.ema_y + a.ema_h
    bx1, by1 = b.x, b.y
    bx2, by2 = b.x + b.w, b.y + b.h

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter = inter_w * inter_h

    area_a = a.ema_w * a.ema_h
    area_b = b.w * b.h
    union = area_a + area_b - inter
    if union <= 0.0:
        return 0.0
    return inter / union


class DetectionStabilizer:
    """
    生検出リストを時系列フィルタリングしてフリッカーを抑える。

    IoU マッチング + 出現/消失ヒステリシス + conf/bbox の EMA + ラベル確定遅延。
    """

    def __init__(
        self,
        enabled: bool = True,
        appear_conf: float = 0.45,
        lose_conf: float = 0.25,
        lose_hold_sec: float = 0.3,
        label_hold_sec: float = 0.3,
        alpha: float = 0.4,
        iou_threshold: float = 0.3,
    ) -> None:
        self._enabled = enabled
        self._appear_conf = appear_conf
        self._lose_conf = lose_conf
        self._lose_hold_sec = lose_hold_sec
        self._label_hold_sec = label_hold_sec
        self._alpha = alpha
        self._iou_threshold = iou_threshold
        self._tracks: list[_Track] = []

    def reset(self) -> None:
        """内部状態をクリアする。"""
        self._tracks = []

    def update(self, dets: list[Detection], now: float) -> list[Detection]:
        """
        生検出リストを受け取り、安定化済みの Detection リストを返す。

        Args:
            dets: 現フレームの生検出。
            now: 現在時刻 (time.time() 等の秒単位)。
        """
        if not self._enabled:
            return list(dets)

        # --- 1. IoU マッチング (greedy、IoU 最大順) ---
        pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(self._tracks):
            for di, det in enumerate(dets):
                threshold = self._iou_threshold if track.label == det.label else _LABEL_CHANGE_IOU
                iou = _iou(track, det)
                if iou >= threshold:
                    pairs.append((iou, ti, di))

        # IoU が高い順に貪欲にペアリング
        pairs.sort(key=lambda x: x[0], reverse=True)
        used_track: set[int] = set()
        used_det: set[int] = set()
        matches: list[tuple[_Track, Detection]] = []
        for _iou_val, ti, di in pairs:
            if ti in used_track or di in used_det:
                continue
            used_track.add(ti)
            used_det.add(di)
            matches.append((self._tracks[ti], dets[di]))

        matched_tracks = {track for track, _det in matches}
        unmatched_dets = [dets[di] for di in range(len(dets)) if di not in used_det]

        # --- 2. マッチした既存トラックの更新 ---
        for track, det in matches:
            track.raw = det
            track.last_matched_now = now
            raw_conf = det.confidence
            good = raw_conf >= self._lose_conf

            if good:
                # 良好状態: 消失候補を解除し、EMA を更新
                track.lost_since = None
                self._update_ema(track, det)
                self._update_label(track, det, now)
            else:
                # conf が低い: 消失候補。表示値は最後の良好な状態を保持
                if track.lost_since is None:
                    track.lost_since = now

            # 未出力トラックが十分な conf を観測したら出力開始
            if not track.active and raw_conf >= self._appear_conf:
                track.active = True

        # --- 3. マッチしなかった既存トラックは消失候補 ---
        for track in self._tracks:
            if track in matched_tracks:
                continue
            if track.lost_since is None:
                track.lost_since = now

        # --- 4. マッチしなかった新規検出からトラックを生成 ---
        for det in unmatched_dets:
            self._tracks.append(_Track(det, now, self._appear_conf))

        # --- 5. 消失期間を超えたトラックを除去 ---
        alive: list[_Track] = []
        for track in self._tracks:
            if track.lost_since is not None and now - track.lost_since >= self._lose_hold_sec:
                continue
            alive.append(track)
        self._tracks = alive

        # --- 6. 出力 (dataclasses.replace で元 Detection を破壊しない) ---
        out: list[Detection] = []
        for track in self._tracks:
            if not track.active:
                continue
            out.append(
                replace(
                    track.raw,
                    label=track.label,
                    label_ja=to_ja(track.label),
                    class_id=track.class_id,
                    confidence=round(track.ema_conf, 4),
                    x=round(track.ema_x, 5),
                    y=round(track.ema_y, 5),
                    w=round(track.ema_w, 5),
                    h=round(track.ema_h, 5),
                )
            )
        return out

    def _update_ema(self, track: _Track, det: Detection) -> None:
        """conf と bbox を EMA で平滑化する。初回は生値そのまま。"""
        a = self._alpha
        track.ema_conf = a * det.confidence + (1.0 - a) * track.ema_conf
        track.ema_x = a * det.x + (1.0 - a) * track.ema_x
        track.ema_y = a * det.y + (1.0 - a) * track.ema_y
        track.ema_w = a * det.w + (1.0 - a) * track.ema_w
        track.ema_h = a * det.h + (1.0 - a) * track.ema_h

    def _update_label(self, track: _Track, det: Detection, now: float) -> None:
        """ラベルが変わった場合、一定期間継続して確定したら更新する。"""
        if track.label == det.label:
            track.label_candidate = None
            return

        # 新しいラベル候補を開始/継続
        if track.label_candidate is None or track.label_candidate[0] != det.label:
            track.label_candidate = (det.label, det.class_id, now)
            track.label_candidate_min_conf = track.ema_conf
        else:
            track.label_candidate_min_conf = min(track.label_candidate_min_conf, track.ema_conf)

            if now - track.label_candidate[2] >= self._label_hold_sec:
                if track.label_candidate_min_conf >= self._appear_conf:
                    track.label = det.label
                    track.class_id = det.class_id
                track.label_candidate = None
