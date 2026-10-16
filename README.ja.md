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

### 同一プロセス内 attach（既定）

`Session` に sink を直結するのが最低遅延のパス（`SessionRunner` なら下の 30 秒の例のように `sink=` に渡す）:

```python
from contingency import ScheduleBuilder
from experiment_core import Session
from session_visualizer import LiveAggregator, NonBlockingEventSink, SessionEventBridge

sink = NonBlockingEventSink(maxsize=4096)
aggregator = LiveAggregator(sink)
aggregator.start()

session = Session(ScheduleBuilder.fr(3))
bridge = SessionEventBridge(session, sinks=[sink])  # 記録もするならここに OklSink を足す
session.start(0.0)
bridge.sync()  # セッションを操作するたびに呼ぶ
```

ライブダッシュボードとして起動する場合:

```
session-visualizer serve --host 127.0.0.1 --port 8765
```

`operantkit-frontend` から `http://127.0.0.1:8765/events` を購読する。

### クロスプロセスの tail

実験プロセスが別プロセスで動く場合 — 別マシン、共有ファイルシステムに
書き込む embedded デバイス、監視配下のサブプロセス等 — オンディスクの
セッションログを tail する。入力源が変わっても SSE/HTTP の API 面は
同一。

```
# 既定: session-recorder が出力する OKL v1
session-visualizer tail path/to/session.txt

# 独自 embedded プロデューサが書く plain JSONL
session-visualizer tail path/to/events.jsonl --format jsonl

# tail 開始前のイベントはスキップ
session-visualizer tail path/to/session.txt --from-end
```

Python API:

```python
# requires a log file that another process is writing
from session_visualizer import (
    JSONLTailReader,
    LiveAggregator,
    LogTailSource,
    NonBlockingEventSink,
    OKLTailReader,
)

sink = NonBlockingEventSink(maxsize=4096)
aggregator = LiveAggregator(sink)
aggregator.start()

# 既定の reader は OKLTailReader。plain JSON なら JSONLTailReader を渡す。
source = LogTailSource("/mnt/embedded-rig/session.okl", sinks=[sink])
source.start()
```

`watchdog` ベースの FS シグナル（tail 遅延を下げる）を使う場合は
``session-visualizer[stream]`` をインストールする。`watchdog` 未導入時は
時間ベースのポーリングにフォールバックする。

### post-hoc 再生

既存の JSONL ログを壁時計速度で再生する場合:

```
session-visualizer replay path/to/session.jsonl --speed 4
```

## このツールの位置づけ

- **該当する:** リアルタイム観測パイプライン（sink → aggregator →
  snapshot → SSE）。実験プロセスを止めずにカウンタ・タイムスタンプを
  外に流す。
- **該当しない:** 正本記録。sink は飽和時にドロップする設計。耐久的な
  保存は `session-recorder` の OKL v1 を使う。
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

## 30 秒で動く例

```bash
pip install git+https://github.com/OperantKit/session-visualizer
pip install session-runner   # 下で使うセッション駆動部
```

待たない sink をセッションに付け、ダッシュボードが描くはずのライブのスナップショットを読む:

```python
from contingency import ScheduleBuilder
from experiment_core import ReinforcementCountExit
from session_runner import SessionConfig, SessionRunner
from session_visualizer import LiveAggregator, NonBlockingEventSink

sink = NonBlockingEventSink(maxsize=4096)  # emit() は待たない。満杯なら捨てて数える
aggregator = LiveAggregator(sink)
aggregator.start()  # 背景スレッドがスナップショットを作る

runner = SessionRunner(
    SessionConfig(name="fr3", schedule=ScheduleBuilder.fr(3), exit_condition=ReinforcementCountExit(count=3)),
    sink=sink,  # 記録と観測を同時に行うなら tee(OklSink("fr3.okl"), sink)
)
runner.start(start_time=0.0)
t = 0.0
while not runner.is_finished:
    t += 1.0
    if runner.is_reinforcing:
        runner.end_reinforcement(t)
    else:
        runner.handle_response(t)

aggregator.stop()  # 残りを取り込んで止める
snapshot = aggregator.snapshot()
print(snapshot.state, len(snapshot.response_times), snapshot.reinforcement_times)
# FINISHED 9 (3.0, 7.0, 11.0)
print(snapshot.sink_stats)
# SinkStats(emitted=23, enqueued=23, dropped=0)
```

累積記録のパネルが描くのは `snapshot.response_times` と `snapshot.reinforcement_times` である。`sink_stats.dropped` で可視化が遅れたかどうかが分かる。`session-visualizer serve` は同じスナップショットを HTTP/SSE で公開する。

## 自分のセッションループからライブ表示を描かない理由

- **実験は表示を待たない.** `NonBlockingEventSink.emit` は上限つきキューへの `put_nowait` で、キューが満杯ならイベントを捨てて数える（`sink_stats.dropped`）。次の反応や強化子を遅らせない。
- **読み出しが生産側と競合しない.** デーモンの `LiveAggregator` がキューを取り込み、不変の `Snapshot` を作る。どのスレッドからでもいつでも `snapshot()` を呼べる。
- **同一プロセスでも別プロセスでも同じ表示.** 動いているセッションに付ける（`SessionEventBridge` か `SessionRunner` の sink）か、別のプロセスや機械が書く OKL v1 / JSONL ファイルを追う（`LogTailSource`、`session-visualizer tail`）。
- **CPU の予算内でのフィット.** 軽いフィットは定期 tick（`PeriodicTicker`、`FitThrottle`）で走り、重いフィットは session-analyzer に渡す。
- **文書化された HTTP 契約.** SSE と図のエンドポイントは `docs/en/http-api.md` に定め、ブラウザ・ノートブック・`curl` から使える。

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
| `[stream]` | `watchdog` | `LogTailSource` 用ファイル監視（tail 遅延を低減） |
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
[`session-analyzer` README](https://github.com/OperantKit/session-analyzer/blob/main/README.ja.md)
を参照。

## 関連パッケージ

- `experiment-core` — `Session`, `EventSink` Protocol, イベント dataclass
- `session-recorder` — 同じイベントストリームの OKL v1 耐久ログ
- `session-analyzer` — OKL v1 ログに対するオフラインのフィットと描画
- `operantkit-frontend` — Next.js UI、`/events` SSE を購読する
