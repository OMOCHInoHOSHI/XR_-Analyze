"""
鑑定文の生成 (Ollama の /api/chat 呼び出し) と、その永続キャッシュの入出力。

生成そのもの(プロンプト組み立て・NDJSONストリーム受信・文分割)を server.py
から切り出したモジュール。単体では Ollama への接続以外の依存を持たないため、
backend/precompute_explain.py (事前生成バッチ) からも同じ関数を呼べる。

永続キャッシュ(JSON)の仕組みの理由: docs/adr/0038-explain-persistent-cache.md
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import config


class ExplainUnavailable(RuntimeError):
    """Ollama へ接続できない・タイムアウトした・応答が壊れているなど、生成自体が実行できないことを表す。"""


class ExplainEmpty(RuntimeError):
    """通信自体は成功したが、生成された本文が空だったことを表す。"""


# 文の区切り。句読点(。！？!?)と改行を区切りとみなし、区切り文字は文に含めたまま切り出す。
_SENTENCE_END_RE = re.compile(r"[。！？!?\n]")


def _split_sentences(buffer: str) -> tuple[list[str], str]:
    """
    ストリーミング中のバッファから確定した文を切り出す。

    区切り文字(。！？!?・改行)を含めたまま各文を返し、まだ区切りが来ていない
    末尾は次回に持ち越すバッファとして返す。空白だけの断片は捨てる。
    """
    sentences: list[str] = []
    start = 0
    for m in _SENTENCE_END_RE.finditer(buffer):
        end = m.end()
        piece = buffer[start:end]
        if piece.strip():
            sentences.append(piece)
        start = end
    return sentences, buffer[start:]


def generate(
    label: str,
    ja: str | None,
    on_sentence: Callable[[str], None] | None = None,
) -> str:
    """
    Ollama の /api/chat を呼び、物体の日本語説明文を得る。

    依存を増やさないため HTTP クライアントは stdlib の urllib で済ませる。
    同期関数なので、呼び出し側(同期defなFastAPIエンドポイント等)がスレッドプールで
    実行すればイベントループを塞がない。

    ストリーミング(stream:True)で受け、一文が確定するたびに on_sentence を呼ぶ
    (与えられていれば)。呼び出し側はここに読み上げの先読み合成キューへの投入を
    包んで渡す想定。鑑定文の生成には数秒かかる(実測: ウォーム時2〜3秒、
    モデル未ロード時は+5〜19秒)ため、その裏で合成を進めておけば、全文が
    画面に出る頃には音声もほぼ揃っている。

    通信失敗・タイムアウト・JSON不正は ExplainUnavailable を、空応答は
    ExplainEmpty を投げる。
    """
    name = ja if ja else label
    # 鑑定士の人物像と2分岐ルール(昔からの道具は素直に/科学の産物は遺物として
    # 正体を明かさない)は実測で調整した。世界観の理由: docs/adr/0024-llm-object-explanation.md
    # 文面は短縮版を使う。224→165トークンでprefillが軽くなり、境界物体でのA/Bでも
    # 判定ルールの遵守に差が無かった (実測: docs/adr/0036-llm-generation-latency.md)
    prompt = (
        "剣と魔法の異世界の「無口な職人鑑定士」として、手元の物体を"
        "日本語2〜3文で淡々と鑑定する。\n"
        f"物体名: {name} ({label})\n"
        "ルール:\n"
        "- 傘・杯・剣・本など昔からある道具 → 用途と価値を素直に語る。"
        "過度な神秘化はしない\n"
        "- PC・携帯・カメラ・車など科学技術の産物 → 「古代文明の遺物」として"
        "正体を明かさず、見た目の観察と推測・畏怖を交えて語る\n"
        "- 感嘆詞(ふむ・ほう等)は使わない。見出し・箇条書き・装飾記法は禁止。"
        "鑑定文のみ出力"
    )
    # think:false は必須。付けないと英語の思考トレースが出て、時間も余計にかかる
    # (理由: docs/adr/0024-llm-object-explanation.md)
    # keep_alive でモデルをGPUメモリに残し、未ロードからの12〜19秒を避ける
    # num_predict は出力の上限。実測の鑑定文は35〜70トークンなので80で足りる
    # (実測: docs/adr/0036-llm-generation-latency.md)
    body = json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": {"temperature": 0.4, "num_predict": 80},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.OLLAMA_URL.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    buffer = ""                  # 区切りがまだ来ていない末尾の断片
    full_parts: list[str] = []   # 確定した文の並び。連結すると全文になる
    try:
        with urllib.request.urlopen(req, timeout=config.EXPLAIN_TIMEOUT_SEC) as resp:
            # NDJSON: 1行1JSON。message.content を連結して全文を組み立てる
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                content = (chunk.get("message") or {}).get("content", "")
                if content:
                    buffer += content
                    sentences, buffer = _split_sentences(buffer)
                    for sentence in sentences:
                        full_parts.append(sentence)
                        if on_sentence is not None:
                            on_sentence(sentence)
                if chunk.get("done"):
                    break
        # 区切り文字が来ないまま終わった末尾も、最後の一文として合成に回す
        if buffer.strip():
            full_parts.append(buffer)
            if on_sentence is not None:
                on_sentence(buffer)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        # HTTPError(接続先の4xx/5xx)は URLError の派生なので、ここでまとめて受ける
        raise ExplainUnavailable(
            "鑑定の力 (Ollama) に接続できません。起動を確認してください (ollama serve)"
        ) from e
    text = "".join(full_parts).strip()
    if not text:
        raise ExplainEmpty("鑑定文の生成が空でした。もう一度お試しください")
    return text


def load_cache(path: str) -> dict[str, str]:
    """
    永続化された鑑定文キャッシュ(label -> text)を読み込む。

    path が空文字なら永続化無効の意味で空dictを返す。ファイルが無い・壊れて
    いる場合でも起動を止めない(空dictを返し、[explain]プレフィクスでログだけ出す)
    (理由: docs/adr/0038-explain-persistent-cache.md。calib_store.load と同じ流儀)
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeDecodeError) as e:
        print(f"[explain] {path} を読めませんでした ({e})。空のキャッシュで起動します。")
        return {}

    if not isinstance(data, dict):
        print(f"[explain] {path} の形式が不正です。空のキャッシュで起動します。")
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def save_cache(path: str, cache: dict[str, str]) -> None:
    """
    鑑定文キャッシュ(label -> text)をJSONへ保存する。

    path が空文字なら何もしない(永続化無効)。一時ファイルへ書いてから
    os.replace でアトミックに差し替える (理由: docs/adr/0038-explain-persistent-cache.md。
    calib_store.save と同じ流儀)。キーでソートするのは差分を git で読みやすくするため。
    """
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)
