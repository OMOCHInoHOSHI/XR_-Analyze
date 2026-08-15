"""
サーバを立てずに「カメラが映るか/認識できるか」を確認するスタンドアロン。

  cd "XR_ Analyze"
  python3 -m backend.check_camera          # 検出あり(YOLO)
  python3 -m backend.check_camera --raw    # 検出なし(素のカメラ映像のみ)

ウィンドウが開く。q または ESC で終了。

グラスを掛けたまま肉眼の視界とカメラ映像を合わせ込むためのライブ調整キー:
  + / =  : ズーム +0.1
  -      : ズーム -0.1 (下限1.0)
  w / s  : 垂直オフセット -0.01 / +0.01 (上 / 下)
  a / d  : 水平オフセット -0.01 / +0.01 (左 / 右)
  0      : ズーム・オフセットをリセット (1.0 / 0 / 0)
  q/ESC  : 終了。終了時にそのままコピペできるenv形式で確定値を表示する。
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
    print(
        "[calib] キー操作: +/- ズーム, w/a/s/d オフセット(上/左/下/右), "
        "0 リセット, q/ESC 終了"
    )
    with Camera(
        index, config.CAM_WIDTH, config.CAM_HEIGHT, config.CAM_FPS, config.CAM_FLIP,
        config.CAM_ZOOM, config.CAM_OFFSET_X, config.CAM_OFFSET_Y,
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
            cv2.putText(
                frame,
                f"zoom={cam.zoom:.2f} off=({cam.offset_x:+.2f},{cam.offset_y:+.2f})",
                (10, 56),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA,
            )

            cv2.imshow("XR Analyze - camera check (q/ESC to quit)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key in (ord("+"), ord("=")):
                cam.set_view(cam.zoom + 0.1, cam.offset_x, cam.offset_y)
            elif key == ord("-"):
                cam.set_view(cam.zoom - 0.1, cam.offset_x, cam.offset_y)
            elif key == ord("w"):
                cam.set_view(cam.zoom, cam.offset_x, cam.offset_y - 0.01)
            elif key == ord("s"):
                cam.set_view(cam.zoom, cam.offset_x, cam.offset_y + 0.01)
            elif key == ord("a"):
                cam.set_view(cam.zoom, cam.offset_x - 0.01, cam.offset_y)
            elif key == ord("d"):
                cam.set_view(cam.zoom, cam.offset_x + 0.01, cam.offset_y)
            elif key == ord("0"):
                cam.set_view(1.0, 0.0, 0.0)

        zoom, off_x, off_y = round(cam.zoom, 4), round(cam.offset_x, 4), round(cam.offset_y, 4)

    cv2.destroyAllWindows()
    print(
        f"[calib] 確定値: CAM_ZOOM={zoom} CAM_OFFSET_X={off_x} CAM_OFFSET_Y={off_y}"
    )
    print(
        f"[calib] 例: CAM_ZOOM={zoom} CAM_OFFSET_X={off_x} CAM_OFFSET_Y={off_y} "
        "python3 -m backend.server"
    )


if __name__ == "__main__":
    main()
