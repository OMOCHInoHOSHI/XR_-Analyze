"""
接続カメラの一覧を表示し、どの index が何のカメラかを特定する。

  cd "XR_ Analyze"
  python3 -m backend.list_cameras

OSが報告するカメラ名(macOS)と、各 index を実際に開いた結果(解像度)を並べる。
InnoMaker は 1080p なので、内蔵カメラと解像度や名前で見分けられる。
ここで分かった番号を CAM_INDEX に入れれば確実に固定できる。
"""
from __future__ import annotations

import cv2

from . import config
from .camera import _backend_for_os, select_camera_index
from .devices import describe, list_camera_names, system_profiler_names


def main(max_index: int = 6) -> None:
    print("=== カメラ名 (AVFoundation = OpenCVのindex順) ===")
    print(describe())

    sp = system_profiler_names()
    if sp:
        print("\n--- 参考: system_profilerの並び (OpenCVと一致しない場合あり) ---")
        for i, n in enumerate(sp):
            print(f"  (sp){i}: {n}")

    print("\n=== 各 index を実際に開いた結果 ===")
    names = list_camera_names()
    backend = _backend_for_os()
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(i)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        name = names[i] if i < len(names) else "(名前不明)"
        status = f"{w}x{h}" if ok and frame is not None else "開いたが読取失敗"
        print(f"  index {i}: {name}  [{status}]")
        cap.release()

    print("\n=== 現在の設定で選ばれるカメラ ===")
    index, reason = select_camera_index(
        config.CAM_INDEX_ENV, config.CAM_NAME, config.CAM_INDEX
    )
    print(f"  -> index {index}  ({reason})")
    print("\n確実に固定したい場合は、上の番号を CAM_INDEX に指定してください。")
    print('  例) CAM_INDEX=1 python3 -m backend.server')


if __name__ == "__main__":
    main()
