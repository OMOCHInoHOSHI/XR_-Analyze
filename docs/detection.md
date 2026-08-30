# 検出モデルと語彙

認識できる物体を増やすためのモデル切り替え、ラベルが安定しないときの語彙の絞り込み、
日本語対訳の網羅方法をまとめる。

## 認識できる物体を増やす

認識できる種類は**モデル**で決まります。用途に応じて `MODEL` を切り替えてください。

### A) モノの名前を幅広く知りたい(専門性不要・推奨)

オープン語彙の「プロンプトフリー」モデルに切り替えます。LVIS **1203カテゴリ**で、
クラスを指定せずに見えたモノへ名前を付けます。**この1203語は全て日本語対訳済み**
(`backend/labels_ja_lvis.py`)なので、表示は日本語になります。

精度優先なら大きいモデルを使います(s < m < l で精度↑/速度↓):

```bash
# 精度優先(推奨): l サイズ これをよく使う
MODEL=yoloe-11l-seg-pf.pt python3 -m backend.server

# 速度優先: s サイズ
MODEL=yoloe-11s-seg-pf.pt python3 -m backend.server
```

- 初回はモデルを自動ダウンロード。`-pf` がプロンプトフリーの印。
- 重い場合は `MAX_DET`(同時表示数)を下げる、`IMG_SIZE=512` 等を併用。
- 辞書外の語(オープン語彙でまれに出る)は英語のまま表示され、`labels_ja_lvis.py`
  に追記すれば日本語化できます。

### B) 検出したい物体を自分で挙げる(テキスト指定)

オープン語彙モデル + `CLASSES` で、挙げた物体だけを検出します。

```bash
MODEL=yoloe-11s-seg.pt CLASSES="wallet,watch,ring,coin,trading card" \
  python3 -m backend.server
```

- `CLASSES` に無い物体は出ません。狙った物だけ拾いたいときに有効。
- テキスト指定モードは初回にテキストエンコーダ(CLIP系)を自動取得する場合があります。

### C) 既定(最軽量)

`MODEL` 未指定なら `yolov8n.pt`(COCO 80カテゴリ)。最速・最小です。

> 補足: いずれも Apple Silicon では自動で `mps`(GPU)が使われます。オープン語彙
> モデルは標準YOLOより重いので、まず `yoloe-11s-...`(s=small)から試すのが無難です。

## ラベルがコロコロ変わるとき (語彙の絞り込み)

オープン語彙モデルの内蔵語彙には、**物体ではない語**が大量に混ざっています。
`yoloe-11l-seg-pf.pt` の 4585 語には `stack` `activity` `accident` `darkness`
`computer room` `street scene` といった語が含まれ、これらが実在の物体名と競合して
毎フレーム最上位ラベルが入れ替わります。暗い部屋にカメラを向けると
「ダークネス」が信頼度0.92で最上位に出る、といった具合です。

既定 (`VOCAB=auto`) では、語彙の大きいモデルに限って **LVIS 1203カテゴリに
一致する語だけ**に絞ります (4585語 → 1054語)。実機フレーム40枚での実測:

| | 最上位ラベルの入れ替わり(生) | 安定化層の通過後 | トップラベル |
|---|---|---|---|
| `VOCAB=all` | 24回 | 6回 | `stack` 21 / `monitor` 9 / `computer chair` 6 |
| `VOCAB=auto` (既定) | **4回** | **0回** | **`monitor` 40/40** |

副次的に速くもなります (91.0ms → 81.9ms)。残したクラスのスコアは絞り込み前と
完全に同じで、消えた語の枠が「許可されている中での最良の名前」へ付け替わります。

**語彙が狭すぎる場合**は、書き出して必要な語を足してください。LVIS に無い物体
(`laptop` `humidifier` `smartphone` など) は名前が付かなくなります。

```bash
python3 -m backend.vocab > my_vocab.txt   # 現在の語彙を書き出す
# my_vocab.txt に laptop などを書き足す (1行1語、# 以降はコメント)
VOCAB=my_vocab.txt python3 -m backend.server
```

絞り込みをやめるには `VOCAB=all` を指定します。詳細と、検討して採らなかった案
(同義語の統合・機械的な語彙拡張) は
[ADR-0018](adr/0018-vocabulary-restriction.md) を参照してください。

## 日本語の語彙を完全網羅する(任意)

YOLOEプロンプトフリーの語彙は約4585語あり、`labels_ja_lvis.py`(LVIS1203)+手動辞書で
カバーしきれない語は英語のまま出ます。残り全部を機械翻訳で一括日本語化できます:

```bash
# 1) 実際の語彙を書き出し
MODEL=yoloe-11m-seg-pf.pt python3 -m backend.dump_vocab
# 2) 翻訳エンジン(どちらか)
pip install argostranslate      # 推奨: オフライン
# pip install deep-translator   # オンライン(Google)
# 3) 未翻訳語を一括翻訳して ja_vocab.json を生成
python3 -m backend.build_ja_dict
```

生成された `ja_vocab.json` はアプリが自動で読み込みます。優先順位は
**機械翻訳 < LVIS < COCO < 手動**です([ADR-0003](adr/0003-label-localization.md))。
気になる訳は `ja_vocab.json` か `backend/labels_ja.py` を直接編集してください。
