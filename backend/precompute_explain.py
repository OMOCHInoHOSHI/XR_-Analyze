"""
全ラベル分の鑑定文をあらかじめ生成し、EXPLAIN_CACHE_PATH (既定:
explain_cache.json) へ永続化する。サーバはこのファイルを起動時に読み込むため、
実行しておけば、生成に約2秒かかる鑑定文の待ちがフロントで発生しなくなる
(理由: docs/adr/0038-explain-persistent-cache.md)。

使い方:
  python3 -m backend.precompute_explain             # COCO 80 語彙
  python3 -m backend.precompute_explain --lvis       # COCO 80 + LVIS 1203
  python3 -m backend.precompute_explain --labels a.txt  # 1行1ラベルのファイルを対象にする
  python3 -m backend.precompute_explain --force      # 既存キャッシュのラベルも生成し直す

事前に Ollama を起動しておくこと (`ollama serve`)。1件あたりウォーム時で
実測約2秒かかるため、COCO 80件でも数分、LVIS 1203件では相応の時間がかかる
見込み (実測: docs/adr/0036-llm-generation-latency.md)。

特徴 (backend/build_ja_dict.py の構造に倣う):
  - 途中保存・再開対応。1件生成するたびに EXPLAIN_CACHE_PATH を上書き保存する
    ので、中断しても既生成分は残り、再実行で続きから進む。
  - 既にキャッシュにあるラベルはスキップする(--force で再生成)。
"""
from __future__ import annotations

import argparse
import time

from . import config
from .explain import ExplainEmpty, ExplainUnavailable, generate, load_cache, save_cache
from .labels_ja import COCO_JA, to_ja

try:
    from .labels_ja_lvis import LVIS_JA
except Exception:
    LVIS_JA = {}


def _collect_labels(args: argparse.Namespace) -> list[str]:
    """
    引数から対象ラベルの一覧を作る。

    --labels が指定された場合はそのファイルの内容だけを対象にする(COCO/LVISは
    使わない)。指定が無ければ既定の COCO 80 を土台にし、--lvis が付いていれば
    LVIS 1203 を追加する(重複は除く)。
    """
    if args.labels:
        with open(args.labels, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    labels = list(COCO_JA.keys())
    if args.lvis:
        seen = set(labels)
        for name in LVIS_JA.keys():
            if name not in seen:
                labels.append(name)
                seen.add(name)
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="鑑定文をラベルごとに事前生成し、EXPLAIN_CACHE_PATH へ永続化する"
    )
    parser.add_argument(
        "--lvis", action="store_true",
        help="LVIS 1203語彙もCOCO 80に追加して対象にする",
    )
    parser.add_argument(
        "--labels", metavar="PATH",
        help="1行1ラベルのファイルを対象にする(指定するとCOCO/LVISの代わりに使う)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="既にキャッシュにあるラベルも生成し直す",
    )
    args = parser.parse_args()

    labels = _collect_labels(args)
    print(f"[precompute] 対象ラベル数: {len(labels)}")

    cache = load_cache(config.EXPLAIN_CACHE_PATH)
    print(f"[precompute] 既存キャッシュ: {len(cache)} 件 ({config.EXPLAIN_CACHE_PATH})")

    todo = labels if args.force else [name for name in labels if name not in cache]
    skipped = len(labels) - len(todo)
    if skipped:
        print(f"[precompute] 既存分をスキップ: {skipped} 件 (--force で再生成)")
    print(f"[precompute] 生成対象: {len(todo)} 件")

    total_start = time.time()
    done = 0
    for i, label in enumerate(todo, 1):
        ja = to_ja(label)
        start = time.time()
        try:
            text = generate(label, ja, on_sentence=None)
        except ExplainUnavailable as e:
            # Ollama に繋がらない状態で残りを回しても無駄なので、ここで打ち切る。
            # ここまでの生成分は既にファイルへ保存済み(1件ごとに保存するため)。
            print(f"[precompute] 中断: {e}")
            print("[precompute] Ollama を起動してから再実行してください (ollama serve)")
            break
        except ExplainEmpty as e:
            # 空応答はそのラベルだけの一過性の問題とみなし、語彙全体は止めない。
            print(f"  [skip] {label!r}: {e}")
            continue

        elapsed = time.time() - start
        cache[label] = text
        save_cache(config.EXPLAIN_CACHE_PATH, cache)
        done += 1
        print(f"  [{i}/{len(todo)}] {label} ({elapsed:.1f}秒, {len(text)}文字)")

    total_elapsed = time.time() - total_start
    avg = total_elapsed / done if done else 0.0
    print(
        f"[precompute] 完了。今回生成 {done} 件 / 合計 {total_elapsed:.1f}秒 / "
        f"平均 {avg:.1f}秒/件。キャッシュ総数 {len(cache)} 件 -> {config.EXPLAIN_CACHE_PATH}"
    )


if __name__ == "__main__":
    main()
