# ADR-0005: カメラは CAM_INDEX > 名前一致 > index 0 の順で選ぶ

- ステータス: 承認済み
- 記録日: 2026-08-18
- 関連: [ADR-0004](0004-config-resolution-order.md)

## 文脈

OpenCV はカメラを整数 index でしか開けず、名前で直接は選べない。
そして index の並び順は**環境依存で安定しない**。実機では
「MacBook Air で内蔵 = index 0、外付け InnoMaker = index 1」というケースと、
逆に「InnoMaker = index 0、内蔵 = index 1」というケースの両方を確認している。
つまり**番号を決め打ちできない**。

外付けの InnoMaker U20CAM-1080P-S1 を使うたびに番号を調べさせるのも避けたい。

名前で解決しようとすると、macOS では並び順の情報源が複数あり、どれを使うかが問題になる。
`system_profiler` が報告する順序は OpenCV (`CAP_AVFOUNDATION`) の index と
**一致しないことがある** (実機で逆順を確認)。

## 決定

- 選択順序を **`CAM_INDEX` の明示 > `CAM_NAME` の部分一致 (既定 `"Innomaker"`) > index 0** とする。
- 名前 → index の解決には、macOS では PyObjC 経由の **AVFoundation 列挙**を使う。
- 名前解決ができない環境では、推測せずに `CAM_INDEX` の明示に委ねる。

## 理由

- **AVFoundation を使う**: OpenCV の `CAP_AVFOUNDATION` の index は `AVCaptureDevice` の
  デバイス順に対応する。同じ AVFoundation から名前を取れば並びが一致する。
  `system_profiler` は人間向けの情報源であり、この対応が保証されない。
- **解決できないときに諦める**: 別の情報源で推測して黙って違うカメラを開くより、
  「名前選択が効かない」と分かる形で `CAM_INDEX` に委ねるほうが事故が少ない。
  選択理由は起動時に `[camera] ...` としてログ出力し、何が選ばれたかを常に見えるようにする。
- **明示を最優先にする**: [ADR-0004](0004-config-resolution-order.md) と同じ方針。

## 結果

- 通常は環境変数なしで外付けカメラが選ばれる。
- `pyobjc-framework-AVFoundation` が requirements.txt の必須依存になる。未導入だと
  名前一致が効かず index 0 へフォールバックする。
- Windows / Linux の名前列挙は環境依存が大きいため未対応。これらの環境では
  `CAM_INDEX` を明示するか、`list_cameras.py` で番号を特定する必要がある。
