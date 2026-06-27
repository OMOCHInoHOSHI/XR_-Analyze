"""
接続カメラの列挙と「名前→index」解決。

OpenCV はカメラを整数 index でしか開けず、名前で直接は選べない。
そこで OS から取得したカメラ名の並び順を、OpenCV の index に対応させる。

  - macOS:   `system_profiler SPCameraDataType -json` でカメラ名を順に取得。
             この並び順は AVFoundation のデバイス順とほぼ一致し、OpenCV の
             CAP_AVFOUNDATION の index に対応する。
  - Windows: 標準コマンドだけでは名前順を確実に取れないため、名前解決は非対応。
             CAM_INDEX を明示するか、list_cameras.py で番号を特定する。

並び順の対応は100%保証ではないので、最終確認は list_cameras.py /
check_camera.py で実映像を見て行うのが確実。
"""
from __future__ import annotations

import json
import platform
import subprocess
from typing import Optional


def list_camera_names() -> list[str]:
    """OSが認識しているカメラ名を、index順(想定)で返す。取得不能なら空リスト。"""
    system = platform.system()
    if system == "Darwin":
        return _macos_camera_names()
    # Windows/Linux の名前列挙は環境依存のため未対応(空を返す)
    return []


def _macos_camera_names() -> list[str]:
    try:
        out = subprocess.run(
            ["system_profiler", "SPCameraDataType", "-json"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        data = json.loads(out)
        items = data.get("SPCameraDataType", [])
        names = [it.get("_name", "") for it in items]
        return [n for n in names if n]
    except Exception:
        return []


def resolve_index_by_name(name: str) -> Optional[int]:
    """
    name(部分一致・大小無視)に一致するカメラの index を返す。
    見つからない / 名前列挙非対応なら None。
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
    """人間向けに、認識中のカメラ名一覧を文字列で返す。"""
    names = list_camera_names()
    if not names:
        return "(カメラ名を列挙できませんでした。list_cameras.py で番号を確認してください)"
    return "\n".join(f"  index {i}: {n}" for i, n in enumerate(names))
