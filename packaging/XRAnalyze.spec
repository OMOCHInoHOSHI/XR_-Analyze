# -*- mode: python ; coding: utf-8 -*-
# PyInstaller の設定 (フォルダ形式)。直接ではなく build_exe.ps1 から使う。
# 構成の理由: docs/adr/0042-windows-exe-packaging.md
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

# 読み取り専用の同梱資源。backend/paths.py の RESOURCE_DIR (= _internal/) 直下に置く。
# 書き換えるもの (explain_cache.json / calib.json / .pt) は build_exe.ps1 が exe の隣へ置く。
datas = [
    (str(ROOT / "frontend"), "frontend"),
    (str(ROOT / "ja_vocab.json"), "."),
]
# ultralytics のモデル定義・データセット定義 (lvis.yaml など) の yaml 群
datas += collect_data_files("ultralytics")

# torchvision の C++ 拡張 (NMS など)。import 文ではなく torch.ops.load_library で
# パッケージのフォルダから読まれるため、PyInstaller の解析では拾われない
# (torchvision 0.29 で `_C.pyd` が `_C_stable.pyd` に改名され、フックも追従していない)。
# 抜けると起動時に "operator torchvision::nms does not exist" で落ちる。
import torchvision

_tv_dir = Path(torchvision.__file__).parent
binaries = [
    (str(p), "torchvision")
    for p in sorted(_tv_dir.iterdir())
    if p.suffix.lower() in (".pyd", ".dll")
]

# 文字列で動的に import されるもの。uvicorn はループ・プロトコル実装を名前で選ぶ。
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("backend")
    + collect_submodules("ultralytics")
    + ["pygrabber.dshow_graph", "comtypes.client"]
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # backend のうち開発・計測用の道具 (CoreML 書き出しは macOS 専用) は要らない
    excludes=["backend.tests", "coremltools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XRAnalyze",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # torch の DLL を UPX で縮めると壊れることがある
    # ログを見せ、閉じれば終了する、という分かりやすさのためにコンソールを残す
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="XRAnalyze",
)
