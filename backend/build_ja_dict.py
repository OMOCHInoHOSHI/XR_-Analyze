"""
モデルの全語彙のうち「まだ日本語化されていない語」を一括で機械翻訳し、
ja_vocab.json (英語 -> 日本語) を生成する。アプリはこのJSONを読むだけなので
実行時はオフライン・高速のまま、語彙を網羅的に日本語化できる。

使い方:
  1) まず語彙を書き出す(未実行なら):
       MODEL=yoloe-11m-seg-pf.pt python3 -m backend.dump_vocab
  2) 翻訳エンジンを入れる(どちらか):
       pip install argostranslate        # 推奨: オフライン・レート制限なし
       # もしくは
       pip install deep-translator        # オンライン(Google)。手軽だが時間がかかる
  3) 生成:
       python3 -m backend.build_ja_dict

特徴:
  - 既に to_ja() で訳せる語(COCO/LVIS/手動辞書)はスキップ。残りだけ翻訳。
  - 途中保存・再開対応(ja_vocab.json を逐次更新)。中断しても続きから。
  - 同音異義(bat/mouse/tank 等)は機械翻訳が外しやすいが、これらはキュレーション
    辞書(labels_ja)が優先されるため最終表示は崩れにくい。気になる語は ja_vocab.json
    を直接編集すればよい。
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from .labels_ja import to_ja

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOCAB_PATH = os.path.join(ROOT, "vocab.json")
OUT_PATH = os.path.join(ROOT, "ja_vocab.json")


def _get_translator() -> tuple[Optional[Callable[[str], str]], str]:
    """利用可能な翻訳関数を返す。(translate_fn, engine_name)。"""
    # 1) argostranslate (オフライン推奨)
    try:
        import argostranslate.package as pkg
        import argostranslate.translate as tr

        installed = {
            (l.code) for l in tr.get_installed_languages()
        }
        if "ja" not in installed or "en" not in installed:
            print("[build] argos: en->ja モデルを取得中...")
            pkg.update_package_index()
            avail = pkg.get_available_packages()
            p = next(x for x in avail if x.from_code == "en" and x.to_code == "ja")
            pkg.install_from_path(p.download())

        langs = tr.get_installed_languages()
        en = next(l for l in langs if l.code == "en")
        ja = next(l for l in langs if l.code == "ja")
        translation = en.get_translation(ja)
        return (lambda s: translation.translate(s)), "argostranslate(offline)"
    except Exception as e:
        print(f"[build] argostranslate 利用不可: {e}")

    # 2) deep-translator (オンライン)
    try:
        from deep_translator import GoogleTranslator

        gt = GoogleTranslator(source="en", target="ja")
        return (lambda s: gt.translate(s)), "deep-translator/Google(online)"
    except Exception as e:
        print(f"[build] deep-translator 利用不可: {e}")

    return None, ""


def main() -> None:
    if not os.path.exists(VOCAB_PATH):
        print(f"vocab.json が見つかりません。先に dump_vocab を実行してください:\n"
              f"  MODEL=yoloe-11m-seg-pf.pt python3 -m backend.dump_vocab")
        return

    vocab = json.load(open(VOCAB_PATH, encoding="utf-8"))
    # 既存の訳でカバーできない語だけ対象
    targets = [n for n in vocab if to_ja(n) == n]
    print(f"[build] 総語彙 {len(vocab)} / 未翻訳 {len(targets)} を処理します")

    # 既存出力を読み込み再開
    result: dict[str, str] = {}
    if os.path.exists(OUT_PATH):
        try:
            result = json.load(open(OUT_PATH, encoding="utf-8"))
            print(f"[build] 既存 ja_vocab.json から {len(result)} 件を再利用")
        except Exception:
            result = {}

    translate, engine = _get_translator()
    if translate is None:
        print("翻訳エンジンがありません。argostranslate か deep-translator を入れてください:")
        print("  pip install argostranslate   # 推奨(オフライン)")
        print("  pip install deep-translator  # オンライン")
        return
    print(f"[build] エンジン: {engine}")

    todo = [n for n in targets if n not in result]
    print(f"[build] 翻訳対象(残り): {len(todo)} 件")

    for i, name in enumerate(todo, 1):
        try:
            ja = translate(name)
            result[name] = ja if ja else name
        except Exception as e:
            print(f"  [skip] {name!r}: {e}")
            result[name] = name
            time.sleep(1.0)  # オンライン時のレート制限対策
        if i % 50 == 0:
            json.dump(result, open(OUT_PATH, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=0)
            print(f"  ...{i}/{len(todo)} 保存")

    json.dump(result, open(OUT_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    print(f"[build] 完了。ja_vocab.json に {len(result)} 件を保存しました。")
    print("[build] サーバ/アプリは次回起動時にこの辞書を自動で読み込みます。")


if __name__ == "__main__":
    main()
