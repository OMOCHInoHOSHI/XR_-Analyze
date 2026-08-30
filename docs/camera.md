# カメラ

どのカメラを使うかの確認方法と、スマートグラス越しの視界に画角を合わせ込む
(ズーム・パン) 手順をまとめる。

## 0. どのカメラが使われるか確認 (内蔵 vs InnoMaker)

既定で **名前一致 `CAM_NAME=Innomaker` により外付けを自動選択**します。これを
macOSで正しく機能させるには `pyobjc-framework-AVFoundation` (requirements.txt 同梱)
が必要です。未導入時は名前選択が効かないため `CAM_INDEX` を明示してください。
(選択順序の設計判断: [ADR-0005](adr/0005-camera-selection.md))

確認:

```bash
python3 -m backend.list_cameras
```

AVFoundation(=OpenCVのindex順)のカメラ名・各 index の実解像度・選ばれる番号を表示。
最終確認は check_camera で実映像を見るのが確実です:

```bash
CAM_INDEX=0 python3 -m backend.check_camera --raw
CAM_INDEX=1 python3 -m backend.check_camera --raw
```

InnoMaker が映った番号を `CAM_INDEX` に指定すれば確実に固定できます。

## 1. まずカメラが映るか確認 (推奨)

```bash
python3 -m backend.check_camera          # YOLO検出つき
python3 -m backend.check_camera --raw    # 素の映像のみ
```

ウィンドウが開きます。`q` または `ESC` で終了。起動時に「どのカメラを選んだか」を
ログ表示します。意図と違う場合は `CAM_INDEX` を指定して再試行してください (configuration.md「設定 (環境変数で上書き)」参照)。

## カメラをスマートグラスに合わせ込む (ズーム・パン)

カメラをスマートグラスに取り付けると、グラス越しの肉眼の視界とカメラ映像の
画角・位置がズレることがあります。`CAM_ZOOM`(デジタルズーム)と
`CAM_OFFSET_X` / `CAM_OFFSET_Y`(切り抜き中心のオフセット)で調整できます。
パンできる範囲はズーム倍率で決まります(zoom=1.0ならパン不可、1.5なら約±0.17、
2.0なら±0.25)。ズームを先に決めてからオフセットで位置を詰めるとよいです。
グラスを掛けたまま映像を見ながらライブ調整できます。**サーバ稼働中のブラウザ画面**と
`check_camera` のどちらでも調整できます。両者は別プロセスなので調整中の値はその場では
共有されませんが、ブラウザ画面で `s` を押して保存すれば `calib.json` 経由で両方に
引き継がれます。

### サーバ稼働中に調整する (推奨)

`python3 -m backend.server` を起動してブラウザ (`http://127.0.0.1:8100/`) を開き、
実際のAR表示を見ながらキーで調整します。調整モードに入る手段は**`c` の単独キー**と
**円盤経由の `m` → `3`** の2つです(矢印キーは通常時、単体鑑定の対象選択に
使われるため調整は専用モードに隔離してあります。
[ADR-0019](adr/0019-key-binding-policy-v2.md) /
[ADR-0021](adr/0021-mode-wheel-with-calibration.md))。
調整モードに入ったら以下のキーが効きます。

- `c` (または `m` → `3`) : 調整モードの出入り
- 調整モード中:
  - `+` / `=` : ズーム +0.1、`-` : ズーム -0.1(下限1.0)
  - `↑` `↓` `←` `→` : 切り抜き位置を上/下/左/右へ 0.01 ずつ移動
    (`↑` で「より上」が見えるようになる = 映像の中身は下へ動く)
  - `s` : 現在のズーム・位置を保存 (次回起動時に自動で復元)
  - `0` : ズーム・位置をリセット
  - `d` : デバッグ映像のON/OFF (調整と直交するため調整モード中も効く)
  - `m` : **調整モード中もモードの円盤を開ける**。円盤から単体鑑定/広域鑑定を
    選べば、そのまま調整を終えられる
  - `c` / `Esc` : 調整モードを終える
- `m` : モードの円盤を開く (usage.md「モードの切り替え」参照。調整モードの外からでも
  中からでも使える)

現在値は画面左上のHUDに出ます。値を変えるたびに、サーバのターミナルへ
`[calib] CAM_ZOOM=... CAM_OFFSET_X=... CAM_OFFSET_Y=...` が出力されます。

#### 調整結果を次回に引き継ぐ

気に入ったところで `s` を押すと、プロジェクト直下の `calib.json` に保存されます
(HUDに `saved → calib.json`、ターミナルに `[calib] 保存しました -> ...` と出ます)。
次回以降は起動時に自動で読み込まれ、同じ画角で立ち上がります
(保存方式の設計判断: [ADR-0008](adr/0008-calibration-persistence.md)):

```text
[calib] /path/to/XR_ Analyze/calib.json を読み込みました。初期値: CAM_ZOOM=1.3 ...
```

- 保存先を変えたい場合は `CALIB_FILE=/path/to/my_calib.json` を指定します
  (機材ごとに使い分けられます)。
- 保存値を使わず一時的に別の値で試したいときは、環境変数を明示すればそちらが勝ちます:

```bash
CAM_ZOOM=1.3 CAM_OFFSET_X=0.02 CAM_OFFSET_Y=-0.01 python3 -m backend.server
```

- 保存値を捨てて等倍に戻すには、`0` でリセットしてから `s`(上書き保存)、または
  `calib.json` を削除します。
- `calib.json` は機材・装着位置ごとに変わる値なので `.gitignore` 済みです。
- `check_camera` も同じ `calib.json` を初期値として読みます(あちらは `s` が
  「下方向へ移動」に割り当て済みのため、保存はブラウザ画面から行います)。

> 内部的にはブラウザから `POST /calib` (差分 `dzoom` / `dx` / `dy` / `reset`) を
> 呼んでいるので、Unity等の他クライアントからも同じ調整ができます。
> ([ADR-0007](adr/0007-viewport-crop-calibration.md))

### サーバを立てずに調整する

```bash
python3 -m backend.check_camera
```

- `+` / `=` : ズーム +0.1、`-` : ズーム -0.1(下限1.0)
- `w` / `s` : 垂直オフセット 上 / 下、`a` / `d` : 水平オフセット 左 / 右
- `0` : ズーム・オフセットをリセット
- `q` / `ESC` : 終了。終了時に確定値が `CAM_ZOOM=... CAM_OFFSET_X=... CAM_OFFSET_Y=...`
  という、そのままコピペできるenv形式でターミナルに出力されます。これを
  `python3 -m backend.server` の前に付けて起動すれば調整結果が反映されます。

例 (別カメラ・高精度モデルで起動):

```bash
CAM_INDEX=1 MODEL=yolov8s.pt python3 -m backend.server
```
