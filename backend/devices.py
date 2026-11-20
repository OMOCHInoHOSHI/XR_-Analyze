"""
接続カメラの列挙と「名前→index」解決。

OpenCV はカメラを整数 index でしか開けず、名前で直接は選べない。
そこで OS から取得したカメラ名の並び順を、OpenCV の index に対応させる。

重要(macOS):
  AVFoundation(PyObjC) で OpenCV と同じ並び順の名前を得る。未導入・失敗時は
  名前解決を諦め CAM_INDEX 明示に委ねる (理由: docs/adr/0005-camera-selection.md)

  - 必要パッケージ: pyobjc-framework-AVFoundation (requirements.txt に追加済み)
  - 未導入時は list_camera_names() が空を返し、resolve_index_by_name() は None。

重要(Windows):
  DirectShow のデバイス列挙(pygrabber)で名前を得る。OpenCV の CAP_DSHOW も同じ
  列挙(CLSID_VideoInputDeviceCategory)を同じ順に辿るため、並びが index と一致する
  (理由: docs/adr/0041-windows-camera-support.md)

  - 必要パッケージ: pygrabber (requirements.txt に追加済み。Windows のみ)
  - 未導入・失敗時は macOS と同じく空リストに落とす。

  Linux の名前列挙は未対応。CAM_INDEX を明示するか、list_cameras.py で番号を特定する。
"""
from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Optional


def list_camera_names() -> list[str]:
    """
    OpenCV の index 順に一致するカメラ名のリストを返す。取得不能なら空リスト。
    macOS では AVFoundation(PyObjC)、Windows では DirectShow(pygrabber)を使用。
    """
    system = platform.system()
    if system == "Darwin":
        return _avfoundation_names()
    if system == "Windows":
        return _dshow_names()
    # Linux は未対応
    return []


def _dshow_names() -> list[str]:
    """
    DirectShow のビデオ入力デバイス名を、OpenCV CAP_DSHOW の index と同じ順で返す。
    pygrabber が無い/失敗した場合は空リスト。

    COM はスレッドごとに初期化が要る。起動画面の列挙や再接続の名前解決は
    メインスレッド以外から呼ばれるため、ここで初期化してから使う。
    """
    try:
        import comtypes  # type: ignore  # pygrabber の依存
        from pygrabber.dshow_graph import FilterGraph  # type: ignore
    except Exception:
        return []
    initialized = False
    try:
        try:
            comtypes.CoInitialize()
            initialized = True
        except OSError:
            # 既に別の方式(アパートメント)で初期化済みのスレッド。そのまま使える。
            pass
        return [str(n) for n in FilterGraph().get_input_devices()]
    except Exception:
        return []
    finally:
        if initialized:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


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
    信頼できる並び(AVFoundation / DirectShow)が得られない場合は None。
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
            "(カメラ名を列挙できませんでした。\n"
            "   macOS は pyobjc-framework-AVFoundation、Windows は pygrabber が\n"
            "   未導入か、列挙に失敗しています。その場合は CAM_INDEX を明示してください。)"
        )
    return "\n".join(f"  index {i}: {n}" for i, n in enumerate(names))


@dataclass
class CameraInfo:
    """起動画面・list_cameras に出す、実際に開いて確かめたカメラ1台分の情報。"""

    index: int
    name: str                 # OS から取れた名前。取れなければ空文字
    opened: bool              # VideoCapture で開けたか
    readable: bool            # 1枚でも読めたか
    width: int = 0            # 実際に得られた解像度 (要求値どおりとは限らない)
    height: int = 0
    thumbnail: Optional[bytes] = field(default=None, repr=False)  # 見分け用の JPEG

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "opened": self.opened,
            "readable": self.readable,
            "width": self.width,
            "height": self.height,
            "has_thumbnail": self.thumbnail is not None,
        }


def probe_cameras(
    max_index: int,
    width: int,
    height: int,
    fourcc: str = "",
    thumbnail_width: int = 320,
) -> list[CameraInfo]:
    """
    接続中のカメラを実際に開いて確かめ、開けたものだけを index 順に返す。

    名前が取れる環境(macOS/Windows)では名前の数だけ、取れなければ max_index 個まで
    試す。どれが外付けかを見分けられるよう、読めたカメラは縮小した1枚を JPEG で
    添える (理由: docs/adr/0043-startup-selection-screen.md)。
    カメラを掴むので、検出パイプラインが動いている間に呼んではいけない。
    """
    import cv2

    from .camera import open_capture

    names = list_camera_names()
    count = len(names) if names else max_index
    found: list[CameraInfo] = []
    for i in range(count):
        cap = open_capture(i, width, height, 0, fourcc)
        if cap is None:
            continue
        info = CameraInfo(index=i, name=names[i] if i < len(names) else "", opened=True,
                          readable=False)
        try:
            frame = None
            # 開いた直後は空フレームが続くことがあるので数枚まで待つ
            for _ in range(10):
                ok, frame = cap.read()
                if ok and frame is not None:
                    break
                frame = None
            if frame is not None:
                info.readable = True
                info.height, info.width = frame.shape[:2]
                scale = thumbnail_width / max(1, info.width)
                small = cv2.resize(frame, (thumbnail_width, max(1, int(info.height * scale))))
                ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ok:
                    info.thumbnail = buf.tobytes()
        finally:
            cap.release()
        found.append(info)
    return found
