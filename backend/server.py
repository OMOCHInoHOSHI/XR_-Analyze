"""
配信サーバ (FastAPI)。

エンドポイント:
  GET  /            -> フロント(frontend/index.html)があれば配信
  GET  /healthz     -> 稼働確認 + 実測fps
  WS   /ws          -> 検出結果JSONをSTREAM_FPSで配信 (Web/Unity/Android共通の契約)
  GET  /video       -> 注釈付きMJPEG (デバッグ確認用。ブラウザで直接開ける)
                       ?annotate=0 で枠を焼き込まない素の映像 (フロントはこちらを使う)
  POST /calib       -> 稼働中のズーム/切り抜き位置の調整 (グラスの視界合わせ)
  POST /calib/save  -> 現在の調整値をファイルへ保存 (次回起動時に自動で復元)

起動:
  cd "XR_ Analyze"
  python3 -m backend.server
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import calib_store, config
from .detector import Detector
from .pipeline import Pipeline

pipeline: Pipeline | None = None
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    pipeline = Pipeline()
    pipeline.start()
    try:
        yield
    finally:
        if pipeline is not None:
            pipeline.stop()


app = FastAPI(title="XR Analyze backend", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    if pipeline is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    payload = pipeline.detections_payload()
    return {
        "status": "ok",
        "fps": payload["fps"],
        "frame_id": payload["frame_id"],
        "model": config.MODEL,
        "device": config.DEVICE or "auto",
        "source_size": payload["source_size"],
        "error": payload["error"],
    }


@app.get("/")
async def index():
    html = FRONTEND_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return JSONResponse(
        {"message": "backend running", "ws": "/ws", "video": "/video", "health": "/healthz"}
    )


class CalibRequest(BaseModel):
    """ズーム/オフセットの調整量。すべて差分(絶対値ではない)。"""

    dzoom: float = 0.0     # ズーム倍率の増減
    dx: float = 0.0        # 切り抜き中心の水平移動 (全体比、+で右)
    dy: float = 0.0        # 切り抜き中心の垂直移動 (全体比、+で下)
    reset: bool = False    # 真なら差分を無視して zoom=1.0 / offset=0,0 に戻す


@app.post("/calib")
async def calib(req: CalibRequest):
    """
    稼働中にカメラのデジタルズーム/切り抜き位置を調整する(グラスの視界合わせ用)。

    差分で受けクランプ後の確定値を返す設計の理由: docs/adr/0010-client-side-display-policy.md
    """
    if pipeline is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    view = pipeline.adjust_view(req.dzoom, req.dx, req.dy, req.reset)
    # check_camera の終了時出力と同じ形式。次回起動にそのままコピペできる。
    print(
        f"[calib] CAM_ZOOM={view['zoom']} "
        f"CAM_OFFSET_X={view['offset_x']} CAM_OFFSET_Y={view['offset_y']}"
    )
    return view


@app.post("/calib/save")
async def calib_save():
    """
    現在のズーム/切り抜き位置をファイルに保存する(フロントの 's' キー)。

    次回起動時に config が読み込み、初期値として復元する。環境変数
    (CAM_ZOOM / CAM_OFFSET_*)で明示された場合はそちらが優先される。
    """
    if pipeline is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    view = pipeline.view()
    try:
        path = calib_store.save(view["zoom"], view["offset_x"], view["offset_y"])
    except OSError as e:
        # 保存に失敗しても稼働中の調整値はそのまま。フロントに失敗を伝えて終わる。
        print(f"[calib] 保存に失敗しました: {e}")
        return JSONResponse(
            {"status": "error", "message": str(e), **view}, status_code=500
        )
    print(
        f"[calib] 保存しました -> {path} "
        f"(CAM_ZOOM={view['zoom']} CAM_OFFSET_X={view['offset_x']} "
        f"CAM_OFFSET_Y={view['offset_y']})"
    )
    return {"status": "ok", "path": str(path), "file": path.name, **view}


@app.websocket("/ws")
async def ws_detections(ws: WebSocket):
    """検出結果JSONを一定fpsで送り続ける。"""
    await ws.accept()
    interval = 1.0 / max(1, config.STREAM_FPS)
    last_sent = -1
    try:
        while True:
            # 新しいフレームのときだけ組み立てて送る(無駄な再送とJSON化を防ぐ)
            if pipeline is not None and pipeline.frame_id != last_sent:
                payload = pipeline.detections_payload()
                last_sent = payload["frame_id"]
                await ws.send_json(payload)
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    except Exception:
        # クライアント切断時の各種例外は握りつぶして終了
        return


def _mjpeg_generator(annotate: bool):
    boundary = b"--frame"
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY]
    last_sent = -1
    while True:
        if pipeline is None:
            time.sleep(0.05)
            continue
        # 推論fpsがSTREAM_FPSより低いと同じフレームが何度も来る。
        # snapshot() はフレームをコピーするので、送る分が無いなら呼ばない。
        if pipeline.frame_id == last_sent:
            time.sleep(0.005)
            continue
        fid, frame, dets = pipeline.snapshot()
        if frame is None:
            time.sleep(0.02)
            continue
        last_sent = fid
        if annotate:
            frame = Detector.annotate(frame, dets)
        ok, buf = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            continue
        yield (
            boundary + b"\r\n"
            + b"Content-Type: image/jpeg\r\n\r\n"
            + buf.tobytes() + b"\r\n"
        )
        time.sleep(1.0 / max(1, config.STREAM_FPS))


@app.get("/video")
async def video(annotate: bool = True):
    """
    注釈付きMJPEG。`?annotate=0` で枠・ラベルを焼き込まない素の映像になる。

    フロントは annotate=0 で読む (理由: docs/adr/0002-detection-streaming-contract.md)
    """
    return StreamingResponse(
        _mjpeg_generator(annotate),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def main() -> None:
    import uvicorn

    print(f"[XR Analyze] http://{config.HOST}:{config.PORT}  (model={config.MODEL})")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
