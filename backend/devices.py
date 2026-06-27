"""
接続カメラの列挙と「名前→index」解決。

OpenCV はカメラを整数 index でしか開けず、名前で直接は選べない。
そこで OS から取得したカメラ名の並び順を、OpenCV の index に対応させる。

重要(macOS):
  OpenCV(CAP_AVFOUNDATION)の index は `AVCaptureDevice` のデバイス順に対応する。
  一方 `system_profiler` の並び順はこれと**一致しない**ことがある(実機で逆順を確認)。
  そのため名前解決には PyObjC 経由の AVFoundation 列挙を使い、OpenCV と同じ
  並び順の名前を得る。これが使えない場合は名前解決を諦め、CAM_INDEX 明示に委ねる。

  - 必要パッケージ: pyobjc-framework-AVFoundation (requirements.txt に追加済み)
  - 未導入時は list_camera_names() が空を返し、resolve_index_by_name() は None。

  Windows/Linux の名前列挙は環境依存のため未対応。CAM_INDEX を明示するか、
  list_cameras.py で番号を特定する。
"""
from __future__ import annotations

import json
import platform
import subprocess
from typing import Optional


def list_camera_names() -> list[str]:
    """
    OpenCV の index 順に一致するカメラ名のリストを返す。取得不能なら空リスト。
    macOS では AVFoundation(PyObjC)を使用。
    """
    if platform.system() == "Darwin":
        return _avfoundation_names()
    # Windows/Linux は未対応
    return []


def _avfoundation_names() -> list[str]:
    """
    AVFoundation のデバイス順でカメラ名を返す。これは OpenCV CAP_AVFOUNDATION の
    index と同じ並び。PyObjC が無い/失敗した場合は空リスト。
    """
    try:
        import AVFoundation  # type: ignore  # pyobjc-framework-AVFoundation

        devices = AVFoundation.AVCaptureDevice.devicesWithMediaType_(
            AVFoundation.AVMediaTypeVideo
        )
        return [str(d.localizedName()) for d in devices]
    except Exception:
        return []


def system_profiler_names() -> list[str]:
    """
    参考情報用: system_profiler が報告するカメラ名。
    ※OpenCV の index 順とは一致しないことがあるため、選択には使わない。
    """
    if platform.system() != "Darwin":
        return []
    try:
        out = subprocess.run(
            ["system_profiler", "SPCameraDataType", "-json"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        items = json.loads(out).get("SPCameraDataType", [])
        return [it.get("_name", "") for it in items if it.get("_name")]
    except Exception:
        return []


def resolve_index_by_name(name: str) -> Optional[int]:
    """
    name(部分一致・大小無視)に一致するカメラの OpenCV index を返す。
    信頼できる並び(AVFoundation)が得られない場合は None。
    """
    if not name:
        return None
    names = list_camera_names()
    target = name.lower()
    for i, n in enumerate(names):
        if target in n.lower():
            return i
    return None


def describe() -> str:
    """人間向けに、OpenCV index 順のカメラ名一覧を文字列で返す。"""
    names = list_camera_names()
    if not names:
        return (
            "(AVFoundationでカメラ名を列挙できませんでした。\n"
            "   pyobjc-framework-AVFoundation 未導入か、列挙に失敗しています。\n"
            "   その場合は CAM_INDEX を明示してください。)"
        )
    return "\n".join(f"  index {i}: {n}" for i, n in enumerate(names))
