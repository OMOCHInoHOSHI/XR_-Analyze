# ADR-0041: Windows ではカメラ名を DirectShow で列挙し、MJPG で開く

- ステータス: 承認済み
- 記録日: 2026-11-19
- 関連: [ADR-0005](0005-camera-selection.md), [ADR-0035](0035-camera-auto-reconnect.md), [ADR-0042](0042-windows-exe-packaging.md), [ADR-0043](0043-startup-selection-screen.md)

## 文脈

README は「Mac / Windows 両対応」と書いていたが、実際に Windows で動かすと
次の二つが macOS と同じには動かなかった。

- **カメラ名を取れない**: `devices.list_camera_names()` は macOS
  (AVFoundation) にしか実装が無く、Windows では空リストを返していた。そのため
  既定の `CAM_NAME=Innomaker` による外付けカメラの自動選択
  ([ADR-0005](0005-camera-selection.md)) と、再接続時の名前での追従
  ([ADR-0035](0035-camera-auto-reconnect.md)) が Windows では効かず、常に index 0
  (たいていは内蔵カメラ) になっていた。起動画面
  ([ADR-0043](0043-startup-selection-screen.md)) でカメラを名前で見せることもできない。
- **既定の符号化方式**: DirectShow (`cv2.CAP_DSHOW`) は UVC カメラを既定で非圧縮の
  YUY2 で開くことが多い。USB 2.0 の帯域では YUY2 の 1280x720 は 5〜10fps 程度が上限で、
  MJPG なら 30fps が出る (UVC カメラ一般の性質)。

## 決定

- Windows では **pygrabber** (DirectShow の `ICreateDevEnum` でビデオ入力デバイスを
  列挙する) でカメラ名を取る。`requirements.txt` には `sys_platform == "win32"` 限定で
  入れる。未導入・失敗時は macOS と同じく空リストに落とし、`CAM_INDEX` 明示に委ねる。
- カメラを開く処理を `camera.open_capture()` にまとめ、`CAM_FOURCC` (既定: Windows は
  `MJPG`、他は空 = 要求しない) を**解像度より先に**設定する。
- COM はスレッドごとに初期化が要るので、名前の列挙のたびに `CoInitialize` /
  `CoUninitialize` する (起動画面の列挙も再接続も、メインスレッド以外から呼ばれる)。

## 理由

- **並び順が OpenCV の index と一致する**: OpenCV の `CAP_DSHOW` 自身が同じ
  `CLSID_VideoInputDeviceCategory` の列挙を同じ順に辿って index を振る。名前と
  index の対応がずれないことが、名前選択を成り立たせる条件 ([ADR-0005](0005-camera-selection.md)
  が macOS で `system_profiler` を採らなかった理由と同じ)。
- **代替案**: PowerShell の `Get-PnpDevice` / WMI は名前を取れるが、並び順が
  DirectShow の列挙順と一致する保証が無い。Media Foundation (`CAP_MSMF`) に
  切り替える案は、既存の `CAP_DSHOW` 前提 (再接続の挙動も含めて実機で確かめてある)
  を崩すため採らなかった。
- **FOURCC を先に設定する理由**: DirectShow は解像度を設定するとき、その時点の
  符号化方式で対応モードを探す。符号化方式を後から変えると、要求した解像度に
  届かないことがある。

## 結果

- Windows でも `CAM_NAME` による自動選択と、再接続時の名前追従が効くようになった。
  実機 (Windows 11) で `['USB 2.0 Camera', 'OBS Virtual Camera']` のように
  index 順の名前が取れること、別スレッドからも取れることを確認した。
- MJPG の要求は、それに対応しないカメラでは無視される (要求しないのと同じ)。
  支障があれば `CAM_FOURCC=` (空) で要求をやめられる。
- 確認した USB カメラの1台は、MJPG の有無にかかわらず 1280x720 の要求を受けず
  1920x1080 を返した。解像度はカメラ側の対応モード次第で、要求どおりになるとは
  限らない (起動画面には実際に得られた解像度を出す)。
- Linux の名前列挙は引き続き未対応。
