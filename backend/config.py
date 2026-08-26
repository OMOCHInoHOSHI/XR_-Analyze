"""
設定値を一箇所に集約。環境変数で上書き可能。
例: CAM_INDEX=1 MODEL=yolov8s.pt python3 -m backend.server
"""
from __future__ import annotations

import os
import platform

from .calib_store import calib_path as _calib_path
from .calib_store import load as _load_saved_view

# サーバ画面で 's' を押して保存した調整値(ズーム/切り抜き位置)。
# 環境変数で明示されていない項目だけがこの値を初期値として使う。
_SAVED_VIEW: dict[str, float] = _load_saved_view()


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


def _bool(name: str, default: bool) -> bool:
    """環境変数を 0/1 整数として読み、bool に変換する。不正値は既定値。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return bool(int(raw))
    except (TypeError, ValueError):
        return default


def _float_view(name: str, saved_key: str, default: float) -> float:
    """
    調整値を「環境変数 > 保存ファイル(calib.json) > 既定値」の優先順位で読む。

    明示指定を最優先にする方針の詳細: docs/adr/0004-config-resolution-order.md
    """
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass  # 不正な値は指定が無かったものとして扱う
    saved = _SAVED_VIEW.get(saved_key)
    return default if saved is None else saved


# --- カメラ ---
# カメラ選択は次の優先順位:
#   1. CAM_INDEX を明示指定 -> その番号を使う (最優先)
#   2. CAM_NAME に名前 -> その名前を含むカメラを自動で探して使う (既定: InnoMaker)
#   3. どちらも解決できなければ index 0
#
# 既定で CAM_NAME="Innomaker" にしてあるのは外付けカメラを狙い撃ちするため (理由: docs/adr/0005-camera-selection.md)
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

# 取得フレームの向き補正。カメラが逆さに取り付けられているため既定で180°回転。
#   "180"  : 上下+左右を反転(= 180°回転)。逆さ付けカメラの正しい補正。
#   "v"    : 上下のみ反転
#   "h"    : 左右のみ反転(鏡像)
#   "none" : 補正しない
# 反転はカメラ読取の時点で行う (理由: docs/adr/0007-viewport-crop-calibration.md)
CAM_FLIP: str = os.environ.get("CAM_FLIP", "180").strip().lower()

# デジタルズーム倍率。1.0で等倍(クロップなし)。1.5なら幅・高さを1/1.5に中央クロップする。
# 1.0未満は余白を足す意味になり無効なので1.0にクランプする。
CAM_ZOOM: float = max(1.0, _float_view("CAM_ZOOM", "zoom", 1.0))

# 切り抜き中心のオフセット。フレーム全体の幅/高さに対する正規化値
# (全体比にする理由: docs/adr/0007-viewport-crop-calibration.md)
# 0.1 = フレーム幅の10%ぶん右へ、-0.1 = 10%ぶん左へ。yは+で下へ。
CAM_OFFSET_X: float = _float_view("CAM_OFFSET_X", "offset_x", 0.0)
CAM_OFFSET_Y: float = _float_view("CAM_OFFSET_Y", "offset_y", 0.0)

# 調整値の保存先(サーバ画面の 's' キーで書き込む)。CALIB_FILE で変更可。
CALIB_FILE: str = str(_calib_path())
if _SAVED_VIEW:
    # 表示するのは環境変数の上書きまで含めた確定値(実際にカメラへ渡る初期値)。
    print(
        f"[calib] {CALIB_FILE} を読み込みました。初期値: "
        f"CAM_ZOOM={CAM_ZOOM} CAM_OFFSET_X={CAM_OFFSET_X} CAM_OFFSET_Y={CAM_OFFSET_Y}"
    )

# --- 検出モデル ---
# 認識できる物体の種類はモデルで決まる:
#   - yolov8n.pt 等(既定)        : COCO 80カテゴリ。最軽量・最速。
#   - yoloe-11s-seg-pf.pt 等       : オープン語彙「プロンプトフリー」。LVIS等1200+の
#                                    語彙で、クラス指定なしに見えたモノへ名前を付ける。
#                                    「専門性不要・モノの名前が分かればいい」用途に最適。
#   - yoloe-11s-seg.pt + CLASSES   : テキスト指定モード。CLASSESで挙げた任意の物体だけ
#                                    を検出(例: CLASSES="wallet,watch,ring")。
# モデル(.pt)は初回に自動ダウンロードされる。
MODEL: str = os.environ.get("MODEL", "yolov8n.pt")

# オープン語彙(YOLOE/YOLO-World)用: 検出したいクラス名をカンマ区切りで指定。
# 指定すると set_classes() でそのクラスだけに絞る。空なら未使用
# (= 通常モデルのCOCO80、またはプロンプトフリーモデルの内蔵語彙のまま)。
_classes_raw: str = os.environ.get("CLASSES", "").strip()
CLASSES: list[str] = (
    [c.strip() for c in _classes_raw.split(",") if c.strip()] if _classes_raw else []
)

CONF_THRES: float = _float("CONF_THRES", 0.35)   # 信頼度しきい値
IOU_THRES: float = _float("IOU_THRES", 0.45)
# 推論サイズ(小さいほど速い)。640が標準。
IMG_SIZE: int = _int("IMG_SIZE", 640)
# 一度に表示・配信する検出数の上限(信頼度の高い順にN件)。
# 多クラスモデルで表示が増えすぎる/重い時に小さくする。0以下で無制限。
MAX_DET: int = _int("MAX_DET", 15)

# --- 検出安定化 ---
# 時系列フィルタで検出結果のフリッカーを抑制する。STAB_ENABLED=0 で無効。
STAB_ENABLED: bool = _bool("STAB_ENABLED", True)
STAB_APPEAR_CONF: float = _float("STAB_APPEAR_CONF", 0.45)   # 出現確定の生 conf
STAB_LOSE_CONF: float = _float("STAB_LOSE_CONF", 0.25)       # 消失判定の生 conf
STAB_LOSE_HOLD_SEC: float = _float("STAB_LOSE_HOLD_SEC", 0.3)  # 消失ヒステリシス秒数
STAB_LABEL_HOLD_SEC: float = _float("STAB_LABEL_HOLD_SEC", 0.3)  # ラベル確定遅延秒数
STAB_ALPHA: float = _float("STAB_ALPHA", 0.4)                # EMA 係数(大きいほど追従が速い)
STAB_IOU: float = _float("STAB_IOU", 0.3)                    # 同一ラベルのマッチ閾値


def _auto_device() -> str:
    """
    推論デバイスを決める。
    優先順位: 環境変数 DEVICE > Apple Silicon の MPS > CUDA(NVIDIA GPU) > CPU。

    詳細 (import 保護など): docs/adr/0009-inference-device-selection.md
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
