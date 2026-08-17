"""
検出ラベル(英語)→日本語の変換。

COCO 80カテゴリは全て対訳を用意。オープン語彙(LVIS等1200+)で辞書に無い語は、
英語のまま返す(=未知語フォールバック)。必要に応じて辞書を追記して拡張できる。
"""
from __future__ import annotations

# COCO 80クラス(Ultralytics yolov8の標準名)→日本語
COCO_JA: dict[str, str] = {
    "person": "人",
    "bicycle": "自転車",
    "car": "車",
    "motorcycle": "バイク",
    "airplane": "飛行機",
    "bus": "バス",
    "train": "電車",
    "truck": "トラック",
    "boat": "ボート",
    "traffic light": "信号機",
    "fire hydrant": "消火栓",
    "stop sign": "一時停止標識",
    "parking meter": "パーキングメーター",
    "bench": "ベンチ",
    "bird": "鳥",
    "cat": "猫",
    "dog": "犬",
    "horse": "馬",
    "sheep": "羊",
    "cow": "牛",
    "elephant": "象",
    "bear": "熊",
    "zebra": "シマウマ",
    "giraffe": "キリン",
    "backpack": "リュック",
    "umbrella": "傘",
    "handbag": "ハンドバッグ",
    "tie": "ネクタイ",
    "suitcase": "スーツケース",
    "frisbee": "フリスビー",
    "skis": "スキー板",
    "snowboard": "スノーボード",
    "sports ball": "ボール",
    "kite": "凧",
    "baseball bat": "バット",
    "baseball glove": "グローブ",
    "skateboard": "スケートボード",
    "surfboard": "サーフボード",
    "tennis racket": "テニスラケット",
    "bottle": "ボトル",
    "wine glass": "ワイングラス",
    "cup": "カップ",
    "fork": "フォーク",
    "knife": "ナイフ",
    "spoon": "スプーン",
    "bowl": "ボウル",
    "banana": "バナナ",
    "apple": "りんご",
    "sandwich": "サンドイッチ",
    "orange": "オレンジ",
    "broccoli": "ブロッコリー",
    "carrot": "にんじん",
    "hot dog": "ホットドッグ",
    "pizza": "ピザ",
    "donut": "ドーナツ",
    "cake": "ケーキ",
    "chair": "椅子",
    "couch": "ソファ",
    "potted plant": "鉢植え",
    "bed": "ベッド",
    "dining table": "テーブル",
    "toilet": "トイレ",
    "tv": "テレビ",
    "laptop": "ノートパソコン",
    "mouse": "マウス",
    "remote": "リモコン",
    "keyboard": "キーボード",
    "cell phone": "携帯電話",
    "microwave": "電子レンジ",
    "oven": "オーブン",
    "toaster": "トースター",
    "sink": "流し台",
    "refrigerator": "冷蔵庫",
    "book": "本",
    "clock": "時計",
    "vase": "花瓶",
    "scissors": "はさみ",
    "teddy bear": "ぬいぐるみ",
    "hair drier": "ドライヤー",
    "toothbrush": "歯ブラシ",
}

# オープン語彙でよく出る一般語の追加対訳(任意。辞書外は英語のまま)
EXTRA_JA: dict[str, str] = {
    "wallet": "財布",
    "watch": "腕時計",
    "ring": "指輪",
    "coin": "硬貨",
    "glasses": "眼鏡",
    "sunglasses": "サングラス",
    "hat": "帽子",
    "cap": "キャップ",
    "shoe": "靴",
    "pen": "ペン",
    "pencil": "鉛筆",
    "cup ": "カップ",
    "mug": "マグカップ",
    "plate": "皿",
    "can": "缶",
    "box": "箱",
    "bag": "バッグ",
    "key": "鍵",
    "card": "カード",
    "earrings": "イヤリング",
    "necklace": "ネックレス",
    "bracelet": "ブレスレット",
    "camera": "カメラ",
    "headphones": "ヘッドホン",
    "tablet": "タブレット",
    "monitor": "モニター",
    "lamp": "ランプ",
    "plant": "植物",
    "flower": "花",
    "towel": "タオル",
    "pillow": "枕",
    "guitar": "ギター",
    "ball": "ボール",
    "doll": "人形",
    # YOLOE-pf語彙(LVIS外)で確認された語の即時追加
    "stuffed toy": "ぬいぐるみ",
    "baby bottle": "哺乳瓶",
    "tablet computer": "タブレット",
    "tablet": "タブレット",
    "laptop keyboard": "キーボード",
    "insulated thermos": "水筒",
    "thermos": "水筒",
}

try:
    from .labels_ja_lvis import LVIS_JA  # LVIS 1203カテゴリの対訳
except Exception:
    LVIS_JA = {}


def _load_machine_dict() -> dict[str, str]:
    """
    build_ja_dict.py が生成した ja_vocab.json(機械翻訳)を読み込む。
    キーは小文字化して取り込む。最優先度は低く(下の統合で上書きされる)、
    キュレーション辞書(LVIS/COCO/手動)が常に優先される。
    """
    import json
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "ja_vocab.json")
    if not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
        return {str(k).strip().lower(): v for k, v in raw.items() if v}
    except Exception:
        return {}


# 統合辞書(後勝ち=後ろほど優先):
#   機械翻訳(ja_vocab) < LVIS < COCO < 手動(EXTRA)
# (理由: docs/adr/0003-label-localization.md)
_TABLE: dict[str, str] = {
    **_load_machine_dict(),
    **LVIS_JA,
    **COCO_JA,
    **EXTRA_JA,
}


def to_ja(name: str) -> str:
    """
    英語ラベルを日本語へ。辞書に無ければ元の英語をそのまま返す。
    LVIS名は "aerosol can/spray can" のように同義語が "/" 区切りで来るため、
    フル文字列 → 最初の同義語 の順で照合する。
    """
    if not name:
        return name
    key = name.strip().lower()
    if key in _TABLE:
        return _TABLE[key]
    # 最初の同義語(最初の "/" より前)で再照合
    first = key.split("/", 1)[0].strip()
    return _TABLE.get(first, name)
