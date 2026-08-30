# ADR-0034: 終了の合図でストリームを自分から閉じる

- ステータス: 承認済み
- 記録日: 2026-08-30
- 関連: [ADR-0002](0002-detection-streaming-contract.md), [ADR-0006](0006-single-background-pipeline.md), [ADR-0032](0032-prompt-shutdown.md)

## 文脈

[ADR-0032](0032-prompt-shutdown.md) で `timeout_graceful_shutdown` を入れ、終了が
待たされる問題は解けた。しかし副作用として、`Ctrl+C` のたびにトレースバックが
出るようになった。

```
INFO:     Waiting for connections to close. (CTRL+C to force quit)
ERROR:    Cancel 1 running task(s), timeout graceful shutdown exceeded
ERROR:    Exception in ASGI application
  ... starlette/responses.py ... listen_for_disconnect ...
asyncio.exceptions.CancelledError: Task cancelled, timeout graceful shutdown exceeded
```

原因は `/video` の MJPEG 配信 (`_mjpeg_generator`) である。これはクライアントが
繋いでいる限り終わらない無限ループなので、終了時は**必ず**「猶予を待つ → 強制
キャンセル」の経路に入る。プロセスは正常に終了しているが、毎回トレースバックが
出るログでは、本当の異常が起きたときに見分けが付かない。

## 決定

- モジュールに停止フラグ (`threading.Event`) を持ち、**`uvicorn.Server.handle_exit` を
  包んで、シグナルを受けた瞬間にフラグを立てる**。そのために `uvicorn.run()` を
  やめ、`uvicorn.Config` と `uvicorn.Server` を自分で組み立てる。
- `/video` の MJPEG ループと `/ws` の配信ループは、毎周フラグを見て自分から抜ける。
- [ADR-0032](0032-prompt-shutdown.md) の `timeout_graceful_shutdown` は**保険として
  残す**。

## 理由

- **`lifespan` の終了処理では遅い**: `lifespan` の `finally` が走るのは「接続の終了
  待ち」が終わったあとで、その時点で既に強制キャンセルは済んでいる。フラグを立てる
  なら、シグナルを受けた瞬間でなければ意味がない。だから `handle_exit` を包む。
- **ストリームは自分で終わるのが筋**: 無限ループを外から切るのは最後の手段である。
  終わる合図を受け取れるなら、自分で後始末をして抜けるほうが正しい。例外を投げずに
  済み、ログも静かになる。
- **`timeout_graceful_shutdown` を残した**: 何かの理由でループが抜けられなかった
  場合の最後の砦になる。[ADR-0032](0032-prompt-shutdown.md) の「止めると決めた後は
  待たない」という決定は生きている。二重の備えであって、置き換えではない。
- **`uvicorn.run()` をやめた**: `handle_exit` に手を入れるには `Server` の実体を
  自分で持つ必要がある。`uvicorn.run()` は内部で組み立てて走らせるだけなので、
  同じ設定を明示的に書き下すだけで済む。

## 結果

- `/video` や `/ws` を繋いだ状態で終了しても、トレースバックが出なくなった。
  終了時間も悪化していない (`/video` 接続中 0.67 秒、`/ws` 接続中 0.66 秒、
  鑑定文の生成中 0.70 秒、`/video` を繋いだまま鑑定文を生成中という重ねた条件でも
  0.79 秒。[ADR-0032](0032-prompt-shutdown.md) の実測 0.99 秒よりむしろ速い)。
- **引き受けた制約**: `uvicorn.Server.handle_exit` という内部寄りの API に依存する。
  uvicorn の更新で名前や呼ばれ方が変われば、ここを直す必要がある。壊れたときは
  「終了時にトレースバックが戻る」という分かりやすい形で出る。
- **引き受けた制約**: 停止フラグはプロセス内の状態なので、`/video` と `/ws` の
  ループが「フラグを見る頻度」だけ反応が遅れる。どちらも 5〜70 ミリ秒ごとに回って
  いるため、体感できる遅れにはならない。
