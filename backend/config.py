"""
設定値を一箇所に集約。環境変数で上書き可能。
例: CAM_INDEX=1 MODEL=yolov8s.pt python3 -m backend.server
"""
from __future__ import annotations

import os
import platform


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- カメラ ---
# カメラ選択は次の優先順位:
#   1. CAM_INDEX を明示指定 -> その番号を使う (最優先)
#   2. CAM_NAME に名前 -> その名前を含むカメラを自動で探して使う (既定: InnoMaker)
#   3. どちらも解決できなければ index 0
#
# MacBook Air では内蔵カメラが index 0、外付けInnoMakerは通常 index 1。
# 既定で CAM_NAME="Innomaker" にしてあるので、名前一致で外付けを狙い撃ちする。
# CAM_INDEX_ENV が None = 環境変数で明示されていない、を意味する。
CAM_INDEX_ENV: int | None = (
    int(os.environ["CAM_INDEX"]) if os.environ.get("CAM_INDEX", "").strip().isdigit()
    else None
)
# 後方互換: 既存コードが参照する CAM_INDEX。明示が無ければフォールバック値0。
CAM_INDEX: int = CAM_INDEX_ENV if CAM_INDEX_ENV is not None else 0
# 名前一致で選ぶ対象(部分一致・大文字小文字無視)。空文字なら名前選択を使わない。
CAM_NAME: str = os.environ.get("CAM_NAME", "Innomaker")
CAM_WIDTH: int = _int("CAM_WIDTH", 1280)
CAM_HEIGHT: int = _int("CAM_HEIGHT", 720)
CAM_FPS: int = _int("CAM_FPS", 30)

# --- 検出モデル ---
# COCO学習済みの軽量モデル。n<s<m<l<x で精度↑/速度↓。
MODEL: str = os.environ.get("MODEL", "yolov8n.pt")
CONF_THRES: float = _float("CONF_THRES", 0.35)   # 信頼度しきい値
IOU_THRES: float = _float("IOU_THRES", 0.45)
# 推論サイズ(小さいほど速い)。640が標準。
IMG_SIZE: int = _int("IMG_SIZE", 640)
def _auto_device() -> str:
    """
    推論デバイスを決める。
    優先順位: 環境変数 DEVICE > Apple Silicon の MPS > CUDA(NVIDIA GPU) > CPU。

    Apple(Apple Silicon Mac)では MPS(GPU)を自動で有効化する。
    DEVICE 環境変数が指定されていればそれを最優先で使う。
    torch が未インストールでも config を読めるよう、import は try で保護する。
    """
    env = os.environ.get("DEVICE", "").strip()
    if env:
        return env

    is_apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    try:
        import torch  # ultralytics の依存。未導入なら下の heuristic にフォールバック。

        if is_apple_silicon and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    except Exception:
        # torch 未導入時: Apple Silicon なら mps を希望値として返す
        # (実際の可否は推論時に torch が判定する)
        return "mps" if is_apple_silicon else ""


# 'cpu' / 'cuda' / 'mps'(Apple Silicon GPU)。
# 空文字なら ultralytics の自動選択。Apple Silicon では自動で 'mps' になる。
DEVICE: str = _auto_device()

# --- 配信 ---
HOST: str = os.environ.get("HOST", "127.0.0.1")
PORT: int = _int("PORT", 8100)
# WebSocketで検出JSONを送る頻度の上限(fps)。
STREAM_FPS: int = _int("STREAM_FPS", 15)
# MJPEGデバッグ映像のJPEG品質(1-100)。
JPEG_QUALITY: int = _int("JPEG_QUALITY", 70)
