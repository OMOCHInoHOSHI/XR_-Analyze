"""
検出語彙の絞り込み。

プロンプトフリーのモデルは 4585 語を内蔵しているが、その中には `stack` `activity`
`computer room` のような**物体でない語**が多数含まれる。これらが実在の物体名と
競合し、フレームごとに最上位ラベルが入れ替わる原因になっていた
(理由と実測: docs/adr/0018-vocabulary-restriction.md)

ここでは「どのクラスを残すか」だけを決める。実際にヘッドを切り詰めるのは
fast_infer 側。
"""
from __future__ import annotations

import os
from pathlib import Path

# 絞り込みなしを表す設定値。
POLICY_ALL = "all"
# LVIS 1203 カテゴリ(同義語を含む)に一致するものだけを残す設定値。
POLICY_LVIS = "lvis"
# 語彙の大きいモデルのときだけ LVIS で絞る設定値(既定)。
POLICY_AUTO = "auto"
# `auto` で絞り込みに入るクラス数の下限。COCO(80)や自分で挙げた CLASSES は
# すべて実在の物体なので絞る意味が無く、絞ると取りこぼすだけになる。
AUTO_MIN_CLASSES = 1500


def _lvis_terms() -> set[str]:
    """
    LVIS 1203 カテゴリの名前を、同義語まで展開した集合として返す。

    ultralytics 同梱の lvis.yaml (`"aerosol can/spray can"` のように "/" 区切り)
    を第一候補にし、見つからなければ本リポジトリの対訳辞書の見出し語で代用する。
    """
    terms: set[str] = set()
    try:
        import yaml

        from ultralytics.cfg import __file__ as cfg_file

        path = Path(cfg_file).parent / "datasets" / "lvis.yaml"
        names = yaml.safe_load(path.read_text(encoding="utf-8"))["names"]
        values = names.values() if isinstance(names, dict) else names
        for value in values:
            for synonym in str(value).split("/"):
                synonym = synonym.strip().lower()
                if synonym:
                    terms.add(synonym)
    except Exception:
        pass

    if not terms:
        # lvis.yaml を読めない環境向けのフォールバック。同義語は含まれないぶん
        # 残る語彙は少なくなるが、方針(実在の物体だけを残す)は変わらない。
        try:
            from .labels_ja_lvis import LVIS_JA

            terms = {k.strip().lower() for k in LVIS_JA}
        except Exception:
            terms = set()
    return terms


def _file_terms(path: str) -> set[str]:
    """1行1語のテキストファイルを読む。`#` 以降と空行は無視する。"""
    terms: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            terms.add(line)
    return terms


def resolve(names: dict[int, str], policy: str) -> list[int] | None:
    """
    残すクラスの元インデックスを返す。絞り込まない場合は None。

    Args:
        names: モデルのクラスID -> 英語名。
        policy: `all` / `lvis` / 1行1語のファイルパス。

    戻り値のインデックスは元の class_id のまま。配信する class_id を変えないため、
    切り詰めた後もこの対応表で元へ戻す (契約: docs/adr/0002-detection-streaming-contract.md)
    """
    policy = (policy or POLICY_AUTO).strip()
    if policy.lower() in ("", POLICY_ALL, "0", "off", "none"):
        return None

    if policy.lower() == POLICY_AUTO:
        # 物体でない語が混ざるのはオープン語彙の内蔵辞書だけなので、
        # 語彙が大きいモデルに限って絞る。
        if len(names) < AUTO_MIN_CLASSES:
            return None
        policy = POLICY_LVIS

    if policy.lower() == POLICY_LVIS:
        terms = _lvis_terms()
        source = "LVIS 1203カテゴリ(同義語込み)"
    elif os.path.exists(policy):
        terms = _file_terms(policy)
        source = policy
    else:
        print(f"[vocab] 語彙ファイル '{policy}' が見つかりません。絞り込みを行いません。")
        return None

    if not terms:
        print(f"[vocab] {source} から語彙を読めませんでした。絞り込みを行いません。")
        return None

    keep = [i for i in sorted(names) if names[i].strip().lower() in terms]
    if not keep:
        print(f"[vocab] {source} に一致するクラスがありません。絞り込みを行いません。")
        return None

    return keep


def _dump() -> None:
    """
    現在の設定で残る語彙を1行1語で標準出力へ出す。編集の出発点として使う。

        python3 -m backend.vocab > my_vocab.txt   # 出して
        # my_vocab.txt に laptop などを書き足してから
        VOCAB=my_vocab.txt python3 -m backend.server

    LVIS はモデルと呼び名が違う語がある (LVIS "laptop computer" / モデル "laptop") ため、
    自動では拾えない語がある。語単位の包含で機械的に拾い直す案も試したが、
    `bar` `beach` `food` のような汎用語まで混ざってちらつきが戻るので採らなかった
    (実測: docs/adr/0018-vocabulary-restriction.md)
    """
    import sys

    from ultralytics import YOLO

    from . import config

    names = YOLO(config.MODEL).names
    keep = resolve(names, config.VOCAB)
    if keep is None:
        keep = sorted(names)
    for i in keep:
        print(names[i])
    print(f"[vocab] {len(keep)} 語", file=sys.stderr)


if __name__ == "__main__":
    _dump()
