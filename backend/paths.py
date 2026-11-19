"""
ファイルの置き場所を一箇所で決める。

開発時 (`python -m backend.server`) と exe (PyInstaller のフォルダ形式) では
置き場所の考え方が違うため、読むだけの資源と書き込むデータを分けて解決する
(理由: docs/adr/0042-windows-exe-packaging.md)。

- RESOURCE_DIR: 同梱の読み取り専用資源 (frontend/・ja_vocab.json)。
  開発時はプロジェクトルート、exe では `_internal/`。
- DATA_DIR: 利用者が書き換える・増えていくもの (calib.json・鑑定文/音声キャッシュ・
  ダウンロードした検出モデル .pt)。開発時はプロジェクトルート、exe では
  XRAnalyze.exe と同じフォルダ。`_internal/` の中に書かないのは、exe を差し替えた
  ときに利用者の保存値まで消えないようにするため。
"""
from __future__ import annotations

import sys
from pathlib import Path

# PyInstaller でビルドした実行ファイルの中で動いているか。
FROZEN: bool = bool(getattr(sys, "frozen", False))

# backend/ の親。exe では backend パッケージが `_internal/backend/` に展開される扱い
# なので、同じ式で `_internal/` を指す。
RESOURCE_DIR: Path = Path(__file__).resolve().parent.parent

DATA_DIR: Path = Path(sys.executable).resolve().parent if FROZEN else RESOURCE_DIR
