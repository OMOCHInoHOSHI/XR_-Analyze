"""
鑑定文の読み上げ音声をあらかじめ合成し、ディスクの永続キャッシュ
(backend/speak_cache.py, 既定 speak_cache/)へ書き溜めておくスクリプト。

サーバ起動中の初回読み上げは合成に5〜10秒かかる(VOICEVOX)。事前に
まとめて合成しておけば、実運用ではディスクキャッシュから即座に返せる
(理由・容量見積り: docs/adr/0039-speak-persistent-cache.md)。

使い方:
  1) 先に鑑定文そのものを用意する。本スクリプトは鑑定文を生成しない。
     別ブランチで並行実装中の永続キャッシュ (explain_cache.json,
     {label: text} 形式) を既定で読む。
  2) VOICEVOX ENGINE を起動しておく(TTS_BACKEND=voicevox が既定)。
  3) 実行:
       python3 -m backend.precompute_speak
     対象を絞る場合:
       python3 -m backend.precompute_speak --labels labels.txt
     既存ファイルも合成し直す場合:
       python3 -m backend.precompute_speak --force

特徴:
  - 既に speak_cache.path_for の場所にファイルがあればスキップ(--force で再合成)。
  - VOICEVOX に接続できない(tts.TTSUnavailable)場合は即中断する。
  - 各件の所要秒・WAVサイズ・音声長を表示し、最後に合計・平均を出す
    (ディスク容量の見積りに使う)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from io import BytesIO

from . import speak_cache, tts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXPLAIN_JSON = os.path.join(ROOT, "explain_cache.json")


def _load_explain_cache(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        raise SystemExit(
            f"{path} が見つかりません。先に鑑定文の事前生成が必要です"
            "(explain_cache.json を用意してから再実行してください)。"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} の形式が不正です({{label: text}} の JSON を想定)")
    return {str(k): str(v) for k, v in data.items()}


def _load_labels(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _wav_duration_sec(data: bytes) -> float:
    with wave.open(BytesIO(data), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        return frames / rate if rate else 0.0


def main() -> None:
    # ログへリダイレクトしても進捗が逐次見えるよう行バッファにする
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--explain-json", default=DEFAULT_EXPLAIN_JSON,
        help=f"鑑定文の {{label: text}} JSON (既定: {DEFAULT_EXPLAIN_JSON})",
    )
    parser.add_argument(
        "--labels", default=None,
        help="対象ラベルを絞る1行1ラベルのファイル(未指定なら explain-json の全件)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="ディスクキャッシュに既にあっても再合成する",
    )
    args = parser.parse_args()

    explain_cache = _load_explain_cache(args.explain_json)
    labels = _load_labels(args.labels) if args.labels else list(explain_cache.keys())

    targets: list[tuple[str, str]] = []
    missing: list[str] = []
    for label in labels:
        text = explain_cache.get(label)
        if text is None:
            missing.append(label)
            continue
        targets.append((label, text))
    if missing:
        print(f"[precompute_speak] 鑑定文が無いためスキップ: {len(missing)} 件 ({', '.join(missing[:10])}{'...' if len(missing) > 10 else ''})")

    print(f"[precompute_speak] 対象 {len(targets)} 件")

    durations: list[float] = []
    sizes: list[int] = []
    skipped = 0
    for i, (label, text) in enumerate(targets, 1):
        path = speak_cache.path_for(label, text)
        if path.exists() and not args.force:
            print(f"  [{i}/{len(targets)}] {label}: 既存のためスキップ ({path.name})")
            skipped += 1
            continue

        start = time.time()
        try:
            data = tts.synthesize(text)
        except tts.TTSUnavailable as e:
            raise SystemExit(
                f"音声合成エンジンに接続できません: {e}\n"
                "VOICEVOX ENGINE (または対応アプリ) を起動してから再実行してください。"
            )
        elapsed = time.time() - start
        speak_cache.store(label, text, data)

        size = len(data)
        duration = _wav_duration_sec(data)
        durations.append(duration)
        sizes.append(size)
        print(
            f"  [{i}/{len(targets)}] {label}: {elapsed:.2f}秒 / "
            f"{size / 1024:.1f}KB / 音声長 {duration:.1f}秒 -> {path.name}"
        )

    print(f"[precompute_speak] 完了。新規合成 {len(sizes)} 件 / スキップ {skipped} 件")
    if sizes:
        total = sum(sizes)
        print(
            f"[precompute_speak] 合計サイズ: {total / 1024 / 1024:.2f}MB / "
            f"平均サイズ: {total / len(sizes) / 1024:.1f}KB / "
            f"平均音声長: {sum(durations) / len(durations):.1f}秒"
        )


if __name__ == "__main__":
    main()
