"""
読み込んだモデルが実際に出力する全クラス名(語彙)を vocab.json に書き出す。

  MODEL=yoloe-11m-seg-pf.pt python3 -m backend.dump_vocab

YOLOEプロンプトフリーの内蔵語彙はLVISと完全一致しないため、日本語辞書を
「実際の語彙」に合わせて網羅するのに使う。出力された vocab.json を見れば、
どのクラス名が未翻訳かを正確に把握できる。
"""
from __future__ import annotations

import json

from ultralytics import YOLO

from . import config
from .labels_ja import to_ja


def main() -> None:
    print(f"[dump_vocab] モデル読込: {config.MODEL}")
    model = YOLO(config.MODEL)
    names = model.names
    vocab = list(names.values()) if isinstance(names, dict) else list(names)

    # 全語彙を保存
    with open("vocab.json", "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False, indent=0)

    # 未翻訳(=to_jaが英語のまま返す)語を抽出
    untranslated = [n for n in vocab if to_ja(n) == n]
    with open("vocab_untranslated.json", "w", encoding="utf-8") as f:
        json.dump(untranslated, f, ensure_ascii=False, indent=0)

    print(f"[dump_vocab] 総クラス数: {len(vocab)}")
    print(f"[dump_vocab] 未翻訳: {len(untranslated)} 件")
    print("[dump_vocab] vocab.json / vocab_untranslated.json を保存しました")


if __name__ == "__main__":
    main()
