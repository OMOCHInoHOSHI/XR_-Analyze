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
  POST /explain     -> 物体ラベルから日本語の説明文を生成 (Ollama のローカルLLM)

起動:
  cd "XR_ Analyze"
  python3 -m backend.server
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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


# 説明文のキャッシュ (label -> text)。生成は秒単位の処理なので、同一ラベルの
# 再生成はしない。プロセスを起動し直せば消える(恒久保存はしない方針)。
_explain_cache: dict[str, str] = {}


class ExplainRequest(BaseModel):
    """説明を生成する物体。label は検出ラベル(英語)、ja はフロントの日本語表示名(任意)。"""

    label: str
    ja: str | None = None


def _ollama_explain(label: str, ja: str | None) -> str:
    """
    Ollama の /api/chat を呼び、物体の日本語説明文を得る。

    依存を増やさないため HTTP クライアントは stdlib の urllib で済ませる。
    この関数は同期 def なエンドポイントから呼ぶため、FastAPI がスレッドプールで
    実行し、イベントループを塞がない。
    """
    name = ja if ja else label
    # 鑑定士の人物像と2分岐ルール(昔からの道具は素直に/科学の産物は遺物として
    # 正体を明かさない)は実測で調整した。世界観の理由: docs/adr/0024-llm-object-explanation.md
    prompt = (
        "あなたは剣と魔法の異世界の「無口な職人鑑定士」。口数が少なく、"
        "事実と評価のみを淡々と述べる。今まさに手元にある物体を鑑定し、日本語でつぶやく。\n"
        f"物体名: {name} ({label})\n"
        "判定のルール:\n"
        "- 傘・杯・剣・本・果物など、文明の如何を問わず昔から存在しうる道具 → "
        "正体を素直に言い当て、用途や価値を語る。過度な神秘化はしない\n"
        "- 電気・機械・科学技術の産物(PC・携帯・カメラ・車など) → "
        "「古代文明の遺物」「謎の物体」として、正体を直接明かさず、"
        "見た目の観察と推測・畏怖を交えて語る\n"
        "- 2〜3文。感嘆詞(ふむ・ほう等)は使わない\n"
        "- 見出し・太字(*)・箇条書きなどの装飾記法は使わず、鑑定文だけを出力する"
    )
    # think:false は必須。付けないと英語の思考トレースが出て、時間も余計にかかる
    # (理由: docs/adr/0024-llm-object-explanation.md)
    body = json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.4, "num_predict": 150},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.OLLAMA_URL.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.EXPLAIN_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        # HTTPError(接続先の4xx/5xx)は URLError の派生なので、ここでまとめて受ける
        raise HTTPException(
            status_code=503,
            detail="鑑定の力 (Ollama) に接続できません。起動を確認してください (ollama serve)",
        ) from e
    text = (data.get("message") or {}).get("content", "").strip()
    if not text:
        raise HTTPException(
            status_code=503, detail="鑑定文の生成が空でした。もう一度お試しください"
        )
    return text


@app.post("/explain")
def explain(req: ExplainRequest):
    """
    物体ラベルから日本語の説明文を生成する(フロントの説明ウィンドウ用)。

    クラウドAPIではなくローカルLLM(Ollama)にした理由: docs/adr/0024-llm-object-explanation.md
    同一ラベルはキャッシュを返し、生成は1回だけ行う。
    """
    cached = _explain_cache.get(req.label)
    if cached is not None:
        return {"label": req.label, "text": cached}
    text = _ollama_explain(req.label, req.ja)
    _explain_cache[req.label] = text
    return {"label": req.label, "text": text}


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
