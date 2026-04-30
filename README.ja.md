# session-visualizer

:gb: [English README](README.md)

OperantKit の実験セッションに対するベストエフォート**ライブ**可視化層。
`experiment-core` の `Session` に `EventSink` Protocol 経由で接続し、
累積応答記録・強化提示・状態・任意のモデルフィット結果を HTTP/SSE で
配信する。`operantkit-frontend` などのダッシュボードから実験中の様子を
そのまま描画できる。

**HTTP API リファレンス:** [`docs/ja/http-api.md`](docs/ja/http-api.md) —
ブラウザ・ネイティブアプリ・ノートブック・`curl` 等の任意クライアント向けの
言語非依存な契約仕様。

## 設計制約

1. **実験スレッドを絶対にブロックしない。** `NonBlockingEventSink.emit`
   は O(1)、`queue.Queue.put_nowait` ベースで、有界キューが満杯の場合は
   待機せずドロップ（カウンタ増加）する。
2. **可視化は遅れてよい。** `LiveAggregator` がデーモンスレッドで
   キューを drain し、インクリメンタルなスナップショットを維持する。
   スナップショットの読み取りは copy-on-read で、プロデューサと競合しない。
3. **クライアント未接続なら何もしない。** SSE サーバはクライアントが
   購読中のあいだだけ一定間隔でスナップショットを送る。重いフィット
   （GML・需要曲線など）は要求時のみ走り、CPU が逼迫していればスキップ
   してよい。

## 統合例

```python
from experiment_core import Session
from session_visualizer import NonBlockingEventSink, LiveAggregator

sink = NonBlockingEventSink(maxsize=4096)
aggregator = LiveAggregator(sink)
aggregator.start()

session = Session(..., sinks=[recorder_sink, sink])  # session-recorder と併用
session.run()
```

ライブダッシュボードとして起動する場合:

```
session-visualizer serve --host 127.0.0.1 --port 8765
```

`operantkit-frontend` から `http://127.0.0.1:8765/events` を購読する。

既存の JSONL ログを壁時計速度で再生する場合:

```
session-visualizer replay path/to/session.jsonl --speed 4
```

## このツールの位置づけ

- **該当する:** リアルタイム観測パイプライン（sink → aggregator →
  snapshot → SSE）。実験プロセスを止めずにカウンタ・タイムスタンプを
  外に流す。
- **該当しない:** 正本記録。sink は飽和時にドロップする設計。耐久的な
  保存は `session-recorder` の JSONL を使う。
- **該当しない:** 正本の統計解析器。重いフィット（非線形需要曲線、
  EM によるバウト分解、ブートストラップ CI 付きマッチング法則）は
  `session-analyzer` が担う。optional extra `analytics` を入れた場合
  に限り、本パッケージから機会主義的に呼び出す。

### in-process と hand-off の切り分け

| 粒度 | 内容 | 実行場所 | extra |
|---|---|---|---|
| 累積記録・応答ティック・状態 | 毎フレーム描画 | `LiveAggregator` snapshot | (core) |
| 移動窓応答率・IRT 記述統計・log-log GML 傾き | 定期 tick（10 秒 / 1 分等） | `PeriodicTicker` + 軽量フィット | `[fit]` |
| 非線形需要曲線・EM バウト分解・ブートストラップ CI | セッション終了時 / オンデマンド | `session-analyzer` | `[analytics]` |

区分の根拠は **CPU 予算**であって「計算可能かどうか」ではない。
数十個の強化子に対する一般化マッチング法則の傾き（log-log OLS）は
ごく軽量なので定期 tick で走らせてよい。Hursh-Silberberg α の
ブートストラップ推定はそうではない。

## インストール

```
mise exec -- python -m venv .venv

# 最小構成（numpy/scipy 無し）:
.venv/bin/python -m pip install -e .

# 定期 tick の軽量フィット付き（numpy + scipy、log-log OLS 等）:
.venv/bin/python -m pip install -e ".[fit]"

# フル構成（server + fit + analytics hand-off + realtime）:
.venv/bin/python -m pip install -e ".[full]"

# メンテナ:
.venv/bin/python -m pip install -e ".[dev,server]"

# エンドツーエンド利用時の sibling パッケージ:
.venv/bin/python -m pip install -e ../../experiment/experiment-core
.venv/bin/python -m pip install -e ../../analysis/session-analyzer
```

### extras 一覧

| extra | 追加される依存 | 用途 |
|---|---|---|
| `[fit]` | `numpy`, `scipy` | 定期 tick 用の軽量フィット（線形回帰、移動窓） |
| `[realtime]` | （予約） | 将来の非同期トランスポート（WebSocket 等） |
| `[server]` | `fastapi`, `sse-starlette`, `uvicorn` | HTTP/SSE ダッシュボードエンドポイント |
| `[analytics]` | `session-analyzer` (sibling) | 重いフィットへの hand-off |
| `[full]` | 上記すべて | エンドユーザ向け "just works" 構成 |

## テスト

```
.venv/bin/pytest
```

## DSL 駆動の分析提案（`session-analyzer` 経由）

`operantkit-frontend` が実験開始前に「どのパネルを出すか」を決めら
れるように、`session-analyzer` が所有するパネル推薦 API を本サーバか
ら HTTP で公開する。

`session-analyzer` がインストールされている場合のみ endpoint が有効
（`[analytics]` extra または editable sibling インストール）:

```
POST /suggest
Content-Type: application/json

<contingency-dsl の解決済み AST — Program または ScheduleExpr 部分木>
```

レスポンス:

```
{"suggestions": [{"name": "...", "reason": "...", "tier": "light" | "heavy"}, ...]}
```

不正入力は HTTP 200 + `{"suggestions": [], "error": "..."}` を返し、
frontend 側で graceful に空状態を表示できるようにする。

Python API・マッピング表・TypeScript 型定義は
[`session-analyzer` README](../../analysis/session-analyzer/README.ja.md#DSL-駆動の分析提案operantkit-frontend-連携用)
を参照。

## 関連パッケージ

- `experiment-core` — `Session`, `EventSink` Protocol, イベント dataclass
- `session-recorder` — 同じイベントストリームの JSONL 耐久ログ
- `session-analyzer` — JSONL に対するオフラインのフィットと描画
- `operantkit-frontend` — Next.js UI、`/events` SSE を購読する
