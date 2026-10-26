# ADR-0040: 既定の検出モデルを YOLOE-pf (yoloe-11l-seg-pf.pt) にする

- ステータス: 承認済み
- 記録日: 2026-09-20
- 関連: [ADR-0018](0018-vocabulary-restriction.md), [ADR-0024](0024-llm-object-explanation.md), [ADR-0038](0038-explain-persistent-cache.md), [ADR-0039](0039-speak-persistent-cache.md)

## 文脈

`MODEL` の既定は初版から `yolov8n.pt` (COCO 80 カテゴリ) のままだった。一方で
実機では `MODEL=yoloe-11l-seg-pf.pt` を常用しており (`docs/tech-stack.md` にも
「実機で常用」と記されていた)、COCO 80 は使われていない。既定と実態のずれは
次の形で表面化した。

- [ADR-0038](0038-explain-persistent-cache.md) の事前生成を、既定の語彙 (COCO 80)
  を基準に始めてしまい、実際に配信される YOLOE-pf の 1054 語とは別物を作った。
- ドキュメント (`docs/detection.md`、`docs/configuration.md`) が「既定は yolov8n」
  と案内しており、初めて動かす人が鑑定できる物体を 80 種に限られた状態で始める。

## 決定

- `MODEL` の既定を **`yoloe-11l-seg-pf.pt`** にする。`VOCAB=auto`
  ([ADR-0018](0018-vocabulary-restriction.md)) により LVIS に絞った 1054 語が
  実行時の語彙になる。
- `yolov8n.pt` は「COCO 80 に割り切って速度を取る」選択肢として残す
  (`MODEL=yolov8n.pt`)。コードからは消さない。
- 事前生成 ([ADR-0038](0038-explain-persistent-cache.md) /
  [ADR-0039](0039-speak-persistent-cache.md)) は既定でこの語彙を対象にする
  (`vocab.runtime_labels`)。同梱の `explain_cache.json` はこの 1054 語を揃えてある。

## 理由

- **このアプリの目的に合う**: 「見えたモノを鑑定する」用途で対象を 80 種に限る
  理由が無い。[ADR-0024](0024-llm-object-explanation.md) の鑑定文も、語彙が広い
  ほど活きる。
- **実態に合わせた**: 常用しているモデルを既定にすれば、環境変数を付け忘れても
  同じ動作になり、ドキュメント・事前生成・キャッシュの基準も一つになる。
- **l サイズにした (s/m でなく)**: 精度優先で常用してきた実績があり、
  `docs/detection.md` でも推奨としていた。速度が足りない環境では `yoloe-11s-seg-pf.pt`
  に落とせば語彙は同じままキャッシュも共有できる。

## 結果

- `MODEL` 未指定での起動が YOLOE-pf になる。ドキュメント (`README.md`、
  `docs/detection.md`、`docs/configuration.md`、`docs/tech-stack.md`) を合わせた。
- **引き受けた制約**: 初回起動のダウンロードが 6.2MB → 71MB になる。
- **引き受けた制約**: 推論が重い。`yoloe-11l` は 150〜250ms/フレーム
  ([ADR-0017](0017-decoupled-capture-thread.md))、実機で 6.5fps 前後に対し、
  `yolov8n` は 27fps (`docs/performance.md`)。実機ではすでにこの条件で運用している
  ため新たな劣化ではないが、非力な環境では `MODEL=yoloe-11s-seg-pf.pt` か
  `yolov8n.pt` に落とす必要がある。
- **引き受けた制約**: 既存の環境で `MODEL` を明示せず yolov8n を使っていた場合、
  更新後に語彙と速度が変わる。戻すには `MODEL=yolov8n.pt`。
