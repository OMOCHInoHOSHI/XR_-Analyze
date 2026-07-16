"""
サーバを立てずに「カメラが映るか/認識できるか」を確認するスタンドアロン。

  cd "XR_ Analyze"
  python3 -m backend.check_camera          # 検出あり(YOLO)
  python3 -m backend.check_camera --raw    # 検出なし(素のカメラ映像のみ)

ウィンドウが開く。q または ESC で終了。
"""
from __future__ import annotations

import argparse
import time

import cv2

from . import config
from .camera import Camera, select_camera_index
from .detector import Detector


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", action="store_true", help="YOLOを使わず素の映像のみ表示")
    args = ap.parse_args()

    detector = None if args.raw else Detector(
        config.MODEL, config.CONF_THRES, config.IOU_THRES, config.IMG_SIZE,
        config.DEVICE, config.CLASSES,
    )

    index, reason = select_camera_index(
        config.CAM_INDEX_ENV, config.CAM_NAME, config.CAM_INDEX
    )
    print(f"[camera] {reason}")
    with Camera(
        index, config.CAM_WIDTH, config.CAM_HEIGHT, config.CAM_FPS, config.CAM_FLIP,
    ) as cam:
        print(f"camera size = {cam.actual_size}, index = {index}")
        last = time.time()
        fps = 0.0
        while True:
            ok, frame = cam.read()
            if not ok:
                print("フレーム取得失敗。CAM_INDEX を確認してください。")
                break

            if detector is not None:
                dets = detector.detect(frame)
                frame = Detector.annotate(frame, dets)

            now = time.time()
            dt = now - last
            last = now
            if dt > 0:
                fps = 0.8 * fps + 0.2 * (1.0 / dt) if fps else 1.0 / dt
            cv2.putText(
                frame, f"{fps:4.1f} fps", (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA,
            )

            cv2.imshow("XR Analyze - camera check (q/ESC to quit)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
