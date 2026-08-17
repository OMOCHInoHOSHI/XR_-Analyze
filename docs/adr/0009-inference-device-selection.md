# ADR-0009: 推論デバイスを DEVICE > MPS > CUDA > CPU で自動選択する

- ステータス: 承認済み
- 記録日: 2026-08-18
- 関連: [ADR-0004](0004-config-resolution-order.md)

## 文脈

主な開発機は Apple Silicon の Mac である。ultralytics の既定に任せると CPU で走ることが
あり、明示しないと目に見えて遅い。かといって毎回 `DEVICE=mps` を付けさせるのは
実用上の摩擦になる。

また `config.py` は設定を読むだけのツール (`list_cameras` など) からも import されるため、
torch が入っていない状態でも読めなければならない。

## 決定

推論デバイスを **環境変数 `DEVICE` > Apple Silicon の MPS > CUDA > CPU** の順で自動選択する。
torch の import は `try` で保護し、未導入でも `config.py` を読めるようにする。

## 理由

- **自動選択にする**: 既定で速い状態にしておき、明示的に CPU を使いたい場合だけ
  `DEVICE=cpu` を指定させる。逆 (既定 CPU + 明示で GPU) にすると、指定を忘れたときに
  「なぜか遅い」という分かりにくい症状になる。
- **環境変数を最優先にする**: [ADR-0004](0004-config-resolution-order.md) と同じ方針。
- **torch の import を保護する**: 判定自体は torch に問い合わせるのが確実だが、
  未導入で `config.py` ごと落とすのは行き過ぎ。未導入時は Apple Silicon なら `"mps"` を
  希望値として返し、実際の可否は推論時に torch が判定する。

## 結果

- Apple Silicon では `DEVICE` を指定しなくても MPS (GPU) が有効になる。
- `config.py` が torch に依存しないため、設定の読み出しだけを行うツールは torch 無しで動く。
- torch 未導入時に返す `"mps"` は「希望値」であり、実際に使えるかはここでは保証しない。
