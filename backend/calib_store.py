"""
ライブ調整値(ズーム/切り抜き位置)の永続化。

サーバ稼働中にグラスへ合わせ込んだ値をJSONに保存し、次回起動時の初期値として読み込む。
読込・優先順位の詳細: docs/adr/0008-calibration-persistence.md, docs/adr/0004-config-resolution-order.md

保存先: 既定はプロジェクトルートの calib.json (環境変数 CALIB_FILE で変更可)。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

# backend/ の親 = プロジェクトルート。__file__ 基準でパスを決める (理由: docs/adr/0008-calibration-persistence.md)
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "calib.json"

# 保存/読込するキー。view() の戻り値と同じ名前で揃えてある。
_KEYS = ("zoom", "offset_x", "offset_y")


def calib_path() -> Path:
    """保存先パス。CALIB_FILE が空なら既定値。"""
    env = os.environ.get("CALIB_FILE", "").strip()
    return Path(env).expanduser() if env else DEFAULT_PATH


def load(path: Optional[Path] = None) -> dict[str, float]:
    """
    保存済みの調整値を読む。戻り値は zoom/offset_x/offset_y のうち有効だったものだけ。

    ファイルが無い・壊れている場合でも起動を止めない(空dictを返す) (理由: docs/adr/0008-calibration-persistence.md)
    """
    p = calib_path() if path is None else Path(path)
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeDecodeError) as e:
        print(f"[calib] {p} を読めませんでした ({e})。既定値で起動します。")
        return {}

    if not isinstance(data, dict):
        print(f"[calib] {p} の形式が不正です。既定値で起動します。")
        return {}

    out: dict[str, float] = {}
    for key in _KEYS:
        v = data.get(key)
        # bool は int のサブクラスなので明示的に除く
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = float(v)
    return out


def save(
    zoom: float, offset_x: float, offset_y: float, path: Optional[Path] = None
) -> Path:
    """
    調整値を保存し、書き込んだパスを返す。

    一時ファイルへ書いてから os.replace でアトミックに差し替える (理由: docs/adr/0008-calibration-persistence.md)
    """
    p = calib_path() if path is None else Path(path)
    payload = {
        "zoom": round(float(zoom), 4),
        "offset_x": round(float(offset_x), 4),
        "offset_y": round(float(offset_y), 4),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p
