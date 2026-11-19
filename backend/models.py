"""
起動画面に出す「使える検出モデル」の一覧。

手元にある .pt と、名前だけで取得できる既知のモデル(初回に自動ダウンロード)を
合わせて並べる (理由: docs/adr/0043-startup-selection-screen.md)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import DATA_DIR

# 名前だけで指定すれば ultralytics が初回にダウンロードするモデル。
# このアプリの用途 (見えたモノを鑑定する) で意味のある候補だけを、推奨順に並べる。
# 説明文の根拠: docs/detection.md, docs/adr/0040-default-model-yoloe.md
KNOWN_MODELS: list[tuple[str, str]] = [
    ("yoloe-11l-seg-pf.pt", "既定。語彙1200語以上・精度優先 (約71MB)"),
    ("yoloe-11m-seg-pf.pt", "語彙は既定と同じ・精度と速度の中間"),
    ("yoloe-11s-seg-pf.pt", "語彙は既定と同じ・速度優先"),
    ("yolov8n.pt", "COCO 80種のみ・最軽量で最速"),
]

# 検出モデルとして読めない .pt (YOLOE のテキスト指定で使う補助モデルなど) を除く。
_NOT_DETECTORS = ("mobileclip",)


@dataclass
class ModelInfo:
    name: str          # MODEL に渡す名前 (ファイル名)
    path: str          # 実体のパス。未取得なら空文字
    local: bool        # 手元にあるか (False なら初回にダウンロード)
    size_mb: float     # 手元にある場合のファイルサイズ
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "local": self.local,
            "size_mb": self.size_mb,
            "description": self.description,
        }


def search_dirs() -> list[Path]:
    """.pt を探す場所。exe の隣(開発時はプロジェクトルート)と、その models/。"""
    return [DATA_DIR, DATA_DIR / "models"]


def _local_models() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in search_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.pt")):
            if any(tag in p.name.lower() for tag in _NOT_DETECTORS):
                continue
            found.setdefault(p.name, p)  # 同名なら先に見つけた方 (DATA_DIR 直下) を使う
    return found


def list_models(current: str = "") -> list[ModelInfo]:
    """
    既知のモデル (推奨順) → それ以外の手元の .pt の順で返す。
    current (環境変数 MODEL など) がどちらにも無ければ先頭に足す。
    """
    local = _local_models()
    known = dict(KNOWN_MODELS)
    out: list[ModelInfo] = []

    def add(name: str, description: str) -> None:
        p = local.get(name)
        out.append(ModelInfo(
            name=name,
            path=str(p) if p else "",
            local=p is not None,
            size_mb=round(p.stat().st_size / 1e6, 1) if p else 0.0,
            description=description,
        ))

    for name, desc in KNOWN_MODELS:
        add(name, desc)
    for name in local:
        if name not in known:
            add(name, "手元のモデル")
    if current and all(m.name != current and m.path != current for m in out):
        p = Path(current)
        if p.is_file():
            out.insert(0, ModelInfo(current, str(p), True, round(p.stat().st_size / 1e6, 1),
                                    "環境変数 MODEL で指定"))
        else:
            out.insert(0, ModelInfo(current, "", False, 0.0, "環境変数 MODEL で指定"))
    return out


def resolve(name: str) -> str:
    """
    MODEL に渡す名前を実際に読むパスへ解決する。

    名前だけ (ディレクトリを含まない) で、探す場所に実体があればその絶対パスを返す。
    無ければ名前のまま返し、ultralytics にダウンロードさせる (保存先はカレント
    ディレクトリ。exe はカレントを DATA_DIR に移してから起動する)。
    """
    p = Path(name)
    if p.parent == Path(".") and not p.is_file():
        local = _local_models().get(p.name)
        if local is not None:
            return str(local)
    return name
