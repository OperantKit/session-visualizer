# session-visualizer

:gb: [English README](README.md)

session-visualizer は、オペラント実験のセッションを走らせながら見るための道具です。累積記録・強化子の印・セッションの状態・任意の軽いモデルの当てはめを、HTTP でブラウザ・ノートブック・[operantkit-frontend](https://github.com/OperantKit/operantkit-frontend) に配信します。[OperantKit](https://github.com/OperantKit) でセッションを走らせ、セッションの時間精度を損なわずに途中経過を見たい人のためのパッケージです。同じ Python のプロセス内のセッションにつなぐことも、別のプロセスや別の機械が書いているログファイルを追うこともできます。

- **セッションは表示を待ちません。** 出来事は上限のある待ち行列に入ります。表示が追いつかないときは、出来事を遅らせるのではなく、表示から落として数えます。
- **手元のセッションも離れたセッションも同じように見られます。** 自分のプログラムの中のセッションにつなぐことも、増えていく OKL や JSONL のログファイルを追うこともできます。
- **HTTP の窓口が文書化されています。** スナップショット、ライブの配信、累積記録の図があります。[HTTP API リファレンス](docs/ja/http-api.md)を参照してください。

この表示は記録ではありません。残る記録には [session-recorder](https://github.com/OperantKit/session-recorder) を、最終的な分析には [session-analyzer](https://github.com/OperantKit/session-analyzer) を使ってください。AI コーディングエージェント向けの案内は [AGENTS.md](AGENTS.md) にあります。

## インストール

Python 3.11 以上が必要です。session-visualizer はまだ PyPI に登録されていないため、`pip install session-visualizer` は今のところ使えません。session-visualizer と、それが必要とする OperantKit のパッケージを依存の解決をせずに GitHub から入れ、外部ライブラリを足してください。`fastapi`・`sse-starlette`・`uvicorn` は HTTP サーバにだけ、session-runner は下の例にだけ必要です。

```bash
G=git+https://github.com/OperantKit
pip install --no-deps $G/contingency-dsl-py $G/contingency-py $G/experiment-core \
  $G/session-recorder $G/session-runner $G/session-analyzer $G/session-visualizer
pip install numpy scipy matplotlib fastapi sse-starlette uvicorn
```

`pip check` はこれらの開発版どうしのバージョンの食い違いを報告しますが、以下の例の動作には影響しません。

## 例

短い FR 3 のセッションを走らせ、OKL ファイルに記録しながら同時に見ます。

```python
from contingency import ScheduleBuilder
from experiment_core import ReinforcementCountExit
from session_recorder import OklSink
from session_runner import SessionConfig, SessionRunner, tee
from session_visualizer import LiveAggregator, NonBlockingEventSink

sink = NonBlockingEventSink(maxsize=4096)  # emit() は待たない。満杯なら落として数える
aggregator = LiveAggregator(sink)
aggregator.start()  # 裏のスレッドがスナップショットを組み立てる

log = OklSink("fr3.txt")
log.write_header(session_name="fr3", clock_type="VirtualClock", session_start=0.0)
runner = SessionRunner(
    SessionConfig(name="fr3", schedule=ScheduleBuilder.fr(3), exit_condition=ReinforcementCountExit(count=3)),
    sink=tee(log, sink),  # 記録しながら見る
)
runner.start(start_time=0.0)
t = 0.0
while not runner.is_finished:
    t += 1.0
    if runner.is_reinforcing:
        runner.end_reinforcement(t)
    else:
        runner.handle_response(t)

aggregator.stop()  # 最後に残りを処理する
snapshot = aggregator.snapshot()
print(snapshot.state, len(snapshot.response_times), snapshot.reinforcement_times)
# FINISHED 9 (3.0, 7.0, 11.0)
print(snapshot.sink_stats)
# SinkStats(emitted=23, enqueued=23, dropped=0)
```

`snapshot.response_times` と `snapshot.reinforcement_times` が、累積記録のパネルが描く値です。`sink_stats.dropped` で、表示が遅れたかどうかがわかります。同じセッションをブラウザで見るには、ログファイルを配信します。

```bash
session-visualizer tail fr3.txt --port 8765
```

すると `http://127.0.0.1:8765/snapshot` が同じ数（反応 9 回、強化子 3 回、状態 `FINISHED`）を返し、`http://127.0.0.1:8765/figure/cumulative-record` が累積記録を PNG で返します。

## 使い方

### ログファイルを追う

セッションが別のプロセスや別の機械で走っているときは、そのセッションが書くログを追います。HTTP の窓口は、自分のプロセス内のセッションのときと同じです。

```bash
session-visualizer tail session.txt                    # session-recorder が書く OKL のログ
session-visualizer tail events.jsonl --format jsonl    # 自作プログラムが書く素の JSONL
session-visualizer tail session.txt --from-end         # 表示を始める前に書かれた出来事は飛ばす
```

`watchdog`（`pip install watchdog`）を入れると、tail は定期的に見に行く代わりにファイルの変化に反応するため、遅れが小さくなります。Python からは、`LogTailSource` に `OKLTailReader`（既定）か `JSONLTailReader` を渡して同じことができます。

### 同じプロセスのセッションにつなぐ

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

session-runner を使う場合は、上の例のように `sink=` に渡してください。HTTP で配信するには、`session_visualizer.server.build_app(aggregator)` でアプリを作り、uvicorn で走らせます。

### HTTP の窓口

`/snapshot`（今の状態の JSON）、`/events`（スナップショットを流す server-sent events）、`/figure/cumulative-record`、`/figure/irt-coded-cumulative-record`、`/figure/response-rate`、`/theme.json`、`POST /suggest`（[contingency-dsl](https://github.com/OperantKit/contingency-dsl) の式に対して表示する価値のある分析。session-analyzer の[分析の提案](https://github.com/OperantKit/session-analyzer/blob/main/docs/ja/suggestions.md)による）があります。パラメータと応答の形式は [HTTP API リファレンス](docs/ja/http-api.md)にあります。

### セッション中に走るもの

| 何を | いつ | どこで |
|---|---|---|
| 累積記録、反応の印、状態 | 更新のたび | `LiveAggregator` のスナップショット |
| 移動窓の反応率、反応間時間（IRT）の記述統計、両対数の一般化マッチングの傾き | 定期的な更新（たとえば 10 秒か 1 分ごと） | `PeriodicTicker` と軽い当てはめ（`FitThrottle`、`compute_fits`） |
| 非線形の需要曲線、EM によるバウトの分解、ブートストラップの信頼区間 | セッションの後 | session-analyzer |

境目は計算できるかどうかではなく、CPU の負担です。数十回の強化子にもとづく一般化マッチングの傾きは定期的な更新で走らせられるほど軽く、ブートストラップ付きの需要曲線の当てはめはそうではありません。

### 任意の追加パッケージ（extras）

| extra | 追加するもの | 目的 |
|---|---|---|
| `[fit]` | `numpy`、`scipy` | 定期的な軽い当てはめ |
| `[server]` | `fastapi`、`sse-starlette`、`uvicorn` | HTTP サーバ |
| `[stream]` | `watchdog` | `tail` のためのファイル変化の通知 |
| `[realtime]` | なし | パッケージを何も入れません |
| `[full]` | `[fit]`、`[server]`、`[stream]`、`[realtime]` | 上のすべて |

### JSONL のログを再生する

```bash
session-visualizer replay events.jsonl --speed 4
```

JSONL のログを実時間の 4 倍の速さで再生し、最後の数を表示します。

## 制限

- ライブの表示は、待ち行列が満杯になると設計どおり出来事を落とします。セッションの記録ではありません。
- `replay` が読むのは JSONL だけで、OKL は読みません。
- `session-visualizer serve` は、出来事を自分で送り込むプログラムのために、空の表示のサーバを立てます。コマンドラインからセッションを見るには `tail` を使ってください。

## 開発

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest
```

この README と `README.md` の `python` のコードブロックはすべて、`tests/test_readme_examples.py` が実行します。

## ライセンス

MIT（`pyproject.toml` で宣言。リポジトリに LICENSE ファイルはありません）。
