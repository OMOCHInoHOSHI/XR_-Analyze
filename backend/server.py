"""
配信サーバ (FastAPI)。

エンドポイント:
  GET  /            -> フロント(frontend/index.html)があれば配信
  GET  /healthz     -> 稼働確認 + 実測fps
  WS   /ws          -> 検出結果JSONをSTREAM_FPSで配信 (Web/Unity/Android共通の契約)
  GET  /video       -> 注釈付きMJPEG (デバッグ確認用。ブラウザで直接開ける)

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

from . import config
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


@app.websocket("/ws")
async def ws_detections(ws: WebSocket):
    """検出結果JSONを一定fpsで送り続ける。"""
    await ws.accept()
    interval = 1.0 / max(1, config.STREAM_FPS)
    last_sent = -1
    try:
        while True:
            if pipeline is not None:
                payload = pipeline.detections_payload()
                # 新しいフレームのときだけ送る(無駄な再送を防ぐ)
                if payload["frame_id"] != last_sent:
                    last_sent = payload["frame_id"]
                    await ws.send_json(payload)
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    except Exception:
        # クライアント切断時の各種例外は握りつぶして終了
        return


def _mjpeg_generator():
    boundary = b"--frame"
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY]
    while True:
        if pipeline is None:
            time.sleep(0.05)
            continue
        _fid, frame, dets = pipeline.snapshot()
        if frame is None:
            time.sleep(0.02)
            continue
        annotated = Detector.annotate(frame, dets)
        ok, buf = cv2.imencode(".jpg", annotated, encode_params)
        if not ok:
            continue
        yield (
            boundary + b"\r\n"
            + b"Content-Type: image/jpeg\r\n\r\n"
            + buf.tobytes() + b"\r\n"
        )
        time.sleep(1.0 / max(1, config.STREAM_FPS))


@app.get("/video")
async def video():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def main() -> None:
    import uvicorn

    print(f"[XR Analyze] http://{config.HOST}:{config.PORT}  (model={config.MODEL})")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
