# 設計判断の記録 (ADR)

このディレクトリは XR Analyze の **Architecture Decision Record** を置く場所です。
「なぜこう作ったか」はここにあります。「どう使うか」はリポジトリ直下の
[README.md](../../README.md) にあります。

1つのファイルが1つの決定に対応します。決定を変えるときは既存ファイルを書き換えず、
新しい ADR を追加して古い方のステータスを `置換 (ADR-XXXX による)` にしてください。
決定の経緯を消さずに残すことが ADR の目的です。

新規作成は [0000-adr-template.md](0000-adr-template.md) を複製して始めます。

> 記録日はいずれも 2026-08-18 です。実装と README・コード内コメントに散在していた
> 判断を、後から遡って ADR に整理したためで、実際に決めた時点とは異なります。

## 一覧

| # | 決定 | 主な対象 |
|---|---|---|
| [0001](0001-monorepo-layout.md) | バックエンドとフロントを1リポジトリでディレクトリ分離する | リポジトリ全体 |
| [0002](0002-detection-streaming-contract.md) | 検出結果は JSON over WebSocket + 正規化座標を唯一の契約とする | `server.py` / `detector.py` |
| [0003](0003-label-localization.md) | 英語ラベルを契約に保ち、日本語表示名を併送する | `labels_ja.py` / `detector.py` |
| [0004](0004-config-resolution-order.md) | 設定は環境変数で上書きし、調整値は 環境変数 > 保存ファイル > 既定値 で解決する | `config.py` |
| [0005](0005-camera-selection.md) | カメラは CAM_INDEX > 名前一致 > index 0 の順で選ぶ | `config.py` / `devices.py` |
| [0006](0006-single-background-pipeline.md) | 取得と推論を単一の背景スレッドに集約し、最新の1件だけを保持する | `pipeline.py` |
| [0007](0007-viewport-crop-calibration.md) | グラスとの視界合わせはカメラ側の中央クロップで行う | `camera.py` |
| [0008](0008-calibration-persistence.md) | 合わせ込み値を calib.json にアトミックに永続化する | `calib_store.py` |
| [0009](0009-inference-device-selection.md) | 推論デバイスを DEVICE > MPS > CUDA > CPU で自動選択する | `config.py` |
| [0010](0010-client-side-display-policy.md) | 鑑定法と鑑定の固定はクライアント側の表示ポリシーとして閉じる | `frontend/index.html` |
| [0011](0011-gaze-target-selection.md) | 単体鑑定の対象は照準の包含 → 半径内の最近傍で決める | `frontend/index.html` |
| [0012](0012-lock-tracking-by-iou.md) | 固定対象は IoU による簡易追従で引き継ぐ | `frontend/index.html` |
| [0013](0013-overlay-coordinate-basis.md) | 枠は映像が実際に占める矩形を基準に配置する | `frontend/index.html` |
| [0014](0014-box-theme-and-dom-reuse.md) | 意匠は CSS で切り替え、枠の DOM は毎フレーム使い回す | `frontend/index.html` |
| [0015](0015-key-binding-policy.md) | 鑑定法の切替は 2 ストローク、修飾キー付きは触らない | `frontend/index.html` |
| [0016](0016-mask-free-fast-inference.md) | マスクを作らない推論経路を自前で持つ | `fast_infer.py` / `detector.py` |
| [0017](0017-decoupled-capture-thread.md) | カメラ取得を推論から切り離し、専用スレッドで回す | `pipeline.py` / `camera.py` |
| [0018](0018-vocabulary-restriction.md) | 検出語彙を実在の物体だけに絞る | `vocab.py` / `fast_infer.py` |
