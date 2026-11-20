"""
接続カメラの一覧を表示し、どの index が何のカメラかを特定する。

  cd "XR_ Analyze"
  python3 -m backend.list_cameras

OSが報告するカメラ名(macOS / Windows)と、各 index を実際に開いた結果(解像度)を並べる。
InnoMaker は 1080p なので、内蔵カメラと解像度や名前で見分けられる。
ここで分かった番号を CAM_INDEX に入れれば確実に固定できる。
"""
from __future__ import annotations

from . import config
from .camera import select_camera_index
from .devices import describe, probe_cameras, system_profiler_names


def main() -> None:
    print("=== カメラ名 (OpenCVのindex順。macOS=AVFoundation / Windows=DirectShow) ===")
    print(describe())

    sp = system_profiler_names()
    if sp:
        print("
--- 参考: system_profilerの並び (OpenCVと一致しない場合あり) ---")
        for i, n in enumerate(sp):
            print(f"  (sp){i}: {n}")

    print("
=== 各 index を実際に開いた結果 ===")
    found = probe_cameras(
        config.CAM_PROBE_MAX, config.CAM_WIDTH, config.CAM_HEIGHT, config.CAM_FOURCC
    )
    if not found:
        print("  開けるカメラがありませんでした")
    for c in found:
        status = f"{c.width}x{c.height}" if c.readable else "開いたが読取失敗"
        print(f"  index {c.index}: {c.name or '(名前不明)'}  [{status}]")

    print("
=== 現在の設定で選ばれるカメラ ===")
    index, reason = select_camera_index(
        config.CAM_INDEX_ENV, config.CAM_NAME, config.CAM_INDEX
    )
    print(f"  -> index {index}  ({reason})")
    print("
確実に固定したい場合は、上の番号を CAM_INDEX に指定してください。")
    print('  例) CAM_INDEX=1 python3 -m backend.server')
    print("サーバの起動画面 (python3 -m backend.server) でも、同じ一覧から選べます。")


if __name__ == "__main__":
    main()
