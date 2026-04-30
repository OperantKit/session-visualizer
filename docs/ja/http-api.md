# HTTP API リファレンス

:gb: [English](../en/http-api.md)

`session-visualizer` は言語非依存な HTTP + Server-Sent-Events 契約を提供する。
ブラウザダッシュボード・ネイティブアプリ・コマンドラインツール・ノートブック
など、どのようなクライアントもサーバ実装の詳細を知らずにこの契約を消費できる。

本書はその公開契約の正典である。データをどう描画するかは規定しない。
描画はクライアント側の責務。

## 責務境界

- **`session-visualizer`** は HTTP/SSE 表面、in-memory `LiveAggregator`、
  `EventSink` Protocol を所有する。**ブラウザ UI は同梱しない**。
- **クライアント**（`operantkit-frontend`、Jupyter、ネイティブアプリ等）は
  描画・インタラクション・Export ボタン等の UX を所有する。
- **`session-analyzer`** は静的図生成とテーマレジストリを所有する。
  `session-visualizer` は必須依存として analyzer を import し、
  analyzer が所有するロジックを再実装しない。

依存方向は [`apps/SCOPE`](../../../../SCOPE) により確定している:
`session-visualizer → session-analyzer`。逆方向は禁止。

## サーバ起動

```bash
uvicorn session_visualizer.cli:app --host 127.0.0.1 --port 8765
```

以下のエンドポイントは指定した `--host:--port` 配下で提供される。

## エンドポイント一覧

| メソッド | パス | 用途 |
|---------|------|------|
| `GET` | `/snapshot` | 現在の aggregator 状態を一発取得（JSON） |
| `GET` | `/events` | 一定間隔でスナップショットを配信する SSE ストリーム |
| `GET` | `/theme.json` | テーマ一覧または単一テーマ仕様を取得 |
| `GET` | `/figure/cumulative-record` | 静的累積記録図（PNG/SVG/PDF） |
| `GET` | `/figure/irt-coded-cumulative-record` | DRL/DRO 向け IRT コード化累積記録 |
| `GET` | `/figure/response-rate` | 移動窓応答率タイムライン（rpm） |
| `POST` | `/suggest` | DSL AST に対する推奨分析パネル |

全エンドポイントは常に登録される。`session-analyzer` は
`session-visualizer` の必須依存であり、テーマ・図・suggester 実装は
すべて analyzer 側に存在する。表の契約が唯一の契約で、クライアント側で
「どのエンドポイントが登録されているか」をプローブする必要はない。

## `GET /snapshot`

現在の aggregator 状態を JSON 一発で返す。

### レスポンス

`200 OK`、`Content-Type: application/json`:

```json
{
  "response_times": [0.5, 1.2, 2.0],
  "reinforcement_times": [1.3],
  "sink_stats": {"enqueued": 3, "dropped": 0, "queue_high_water": 2},
  "state": "...",
  "fits": {}
}
```

`response_times` と `reinforcement_times` はセッション開始からの秒数。

## `GET /events`

Server-Sent-Events ストリーム。各 tick は `/snapshot` と同じペイロードを
`event: snapshot` として配信する。既定配信間隔は 2 Hz（500 ms ごとに 1 フレーム）。
配信間隔はサーバ構築時に `build_app(push_interval=...)` で固定される。

### レスポンス

`200 OK`、`Content-Type: text/event-stream`:

```
event: snapshot
data: {"response_times": [...], "reinforcement_times": [...], ...}

event: snapshot
data: {...}
```

クライアントは SSE 仕様に従ってトランスポートエラー時に自動再接続する。

## `GET /theme.json`

テーマメタデータを取得する。テーマは宣言的・描画エンジン非依存な仕様として
`session-analyzer` で定義されている。クライアントはこの仕様を各自の描画ライブラリ
（recharts, D3, matplotlib, Plotly, GDI+ 等）の設定に変換する。

### 一覧モード

クエリパラメタなしの `GET /theme.json`:

```json
{"themes": ["jeab-bw", "jeab-bw-marker", "nature-color",
            "preprint-draft", "readable", "readable-dark",
            "science-color"]}
```

### 単一モード

`GET /theme.json?name=<theme-id>`:

```json
{
  "name": "jeab-bw",
  "description": "JEAB / JABA / Behavioural Processes monochrome house style.",
  "font_family": "Helvetica",
  "font_size_pt": 8.0,
  "background": "#ffffff",
  "foreground": "#000000",
  "palette": ["#000000", "#4d4d4d", "#808080", "#b3b3b3"],
  "line_style_cycle": ["-", "--", "-.", ":"],
  "marker_cycle": ["o", "s", "^", "D"],
  "line_width_pt": 1.0,
  "figure_width_in": 3.3,
  "figure_height_in": 2.5,
  "dpi": 300,
  "grid": "none",
  "spine_width_pt": 0.75,
  "legend_frame": false,
  "color_blind_safe": true,
  "intended_use": "Direct paste into JEAB / JABA / Beh. Processes manuscripts.",
  "tags": ["paper", "monochrome", "jeab"]
}
```

### ステータスコード

- `200 OK` — テーマが見つかった（または一覧返却）
- `404 Not Found` — 未知のテーマ名

## `GET /figure/cumulative-record`

サーバ側で matplotlib を使って描画した論文品質の静的図を返す。自前で仕様を
描画できないネイティブクライアントや、正本の論文品質出力が欲しいユーザー向け。

### クエリパラメタ

| 名前 | 型 | 既定 | 備考 |
|------|---|------|------|
| `theme` | string | `readable` | `/theme.json` が返す任意の id |
| `fmt` | string | `png` | `png` / `svg` / `pdf` のいずれか |
| `show_event_pen` | bool | `true` | F&S 式の event pen を累積記録の下に描画するか |
| `wrap` | bool | `true` | ペンのラップの有効/無効。`false` でリセット機構を丸ごと無効化 |
| `reset_responses` | int | *(正典 550)* | `wrap=true` 時のリセット間隔。正整数のみ。`wrap=false` では無視 |

### ステータスコード

- `200 OK` — 描画成功、ボディは生の画像バイト列
- `204 No Content` — スナップショットの応答数がゼロ。描画するものがない
- `400 Bad Request` — 未対応の `fmt` または非正値の `reset_responses`
- `404 Not Found` — 未知の `theme`

図は呼び出し時点の aggregator スナップショットの best-effort コピー。
本エンドポイントの呼び出しは実験スレッドをブロックしない・遅延させない。
スナップショットはコピーオンリードであり、matplotlib は**グローバル状態を触らない
`Figure` インスタンス**上でサーバのスレッドプール内で描画する。

## `GET /figure/irt-coded-cumulative-record`

Ferster & Skinner 方式の IRT コード化累積記録。各応答を直前 IRT が閾値を
超えているかで分類し、長短 2 群を異なるマーカーで描画する。DRL / DRO
パフォーマンスの可視化向け。

### クエリパラメタ

| 名前 | 型 | 既定 | 備考 |
|------|---|------|------|
| `irt_threshold_sec` | float | **必須** | 正値。直前 IRT ≥ 閾値は "long"、それ未満は "short" |
| `theme` | string | `readable` | `/theme.json` が返す任意の id |
| `fmt` | string | `png` | `png` / `svg` / `pdf` のいずれか |
| `show_event_pen` | bool | `true` | F&S 式の event pen を描画するか |
| `reset_responses` | int | *(正典 550)* | ペンリセット間隔。`0` でラップ無効化。負値は拒否 |

### ステータスコード

- `200 OK` — 描画成功、ボディは生の画像バイト列
- `204 No Content` — スナップショットの応答数がゼロ
- `400 Bad Request` — 未対応の `fmt`、`irt_threshold_sec <= 0`、または非正値の `reset_responses`
- `404 Not Found` — 未知の `theme`
- `422 Unprocessable Entity` — `irt_threshold_sec` 未指定

## `GET /figure/response-rate`

移動窓応答率タイムライン。規則的な時間グリッドの各点について、直前の
`window_sec` 秒間に発生した応答数を `window_sec` で割り、毎分当たりに
スケーリングして表示する。スナップショットに強化イベントが含まれる場合は
垂直線として重ね描きする。

### クエリパラメタ

| 名前 | 型 | 既定 | 備考 |
|------|---|------|------|
| `window_sec` | float | **必須** | 正値。窓幅（秒） |
| `step_sec` | float | **必須** | 正値。グリッド刻み（秒） |
| `theme` | string | `readable` | `/theme.json` が返す任意の id |
| `fmt` | string | `png` | `png` / `svg` / `pdf` のいずれか |

### ステータスコード

- `200 OK` — 描画成功、ボディは生の画像バイト列
- `204 No Content` — スナップショットの応答数がゼロ
- `400 Bad Request` — 未対応の `fmt`、`window_sec <= 0`、または `step_sec <= 0`
- `404 Not Found` — 未知の `theme`
- `422 Unprocessable Entity` — 必須パラメタ未指定

## `POST /suggest`

解決済み DSL AST に対して推奨される分析パネル一覧を返す。
`session_analyzer.suggester` の薄い HTTP 表面。AST スキーマは analyzer パッケージを参照。

### リクエスト

`Content-Type: application/json`。ボディは解決済みの `contingency-dsl` Program
または ScheduleExpr サブツリー。

### レスポンス

```json
{"suggestions": [{"name": "...", "reason": "...", "tier": "..."}]}
```

## クライアント実装例

### `curl`

```bash
# 一発スナップショット
curl http://127.0.0.1:8765/snapshot

# テーマ一覧
curl http://127.0.0.1:8765/theme.json

# 現在の累積記録を JEAB スタイルの PDF で取得
curl -o cumrec.pdf "http://127.0.0.1:8765/figure/cumulative-record?theme=jeab-bw&fmt=pdf"

# DRL 5 秒の IRT コード化累積記録を取得
curl -o drl.svg "http://127.0.0.1:8765/figure/irt-coded-cumulative-record?theme=jeab-bw&fmt=svg&irt_threshold_sec=5.0"

# 応答率タイムラインを取得（10 秒窓、1 秒刻み）
curl -o rate.svg "http://127.0.0.1:8765/figure/response-rate?window_sec=10&step_sec=1&theme=nature-color&fmt=svg"
```

### Python（`httpx`）

```python
import httpx

async with httpx.AsyncClient(base_url="http://127.0.0.1:8765") as cli:
    theme = (await cli.get("/theme.json", params={"name": "nature-color"})).json()
    img = (await cli.get("/figure/cumulative-record",
                         params={"theme": "nature-color", "fmt": "svg"})).content
```

### ブラウザ（`fetch` + SSE）

```js
const ev = new EventSource("http://127.0.0.1:8765/events");
ev.addEventListener("snapshot", (e) => {
  const snap = JSON.parse(e.data);
  // snap.response_times を任意の描画ライブラリに流す
});

const theme = await (await fetch("/theme.json?name=readable")).json();
// theme.palette, theme.font_family 等を使うライブラリの設定に変換する
```

### HTTP を叩ける任意の言語

上記の契約は意図的に言語中立に保たれている。HTTP GET が投げられ、JSON が
パースでき（あるいは SSE を購読でき、バイナリをファイルに保存でき）れば
どんなクライアントでも統合できる。言語特化の SDK は同梱しない。
**HTTP 契約そのものが SDK** である。

## 安定性とバージョニング

本書は現行の表面仕様を記述する。エンドポイント形状の破壊的変更はパッケージ
`CHANGELOG` で告知する。JSON レスポンスへのフィールド追加は後方互換とみなし、
クライアントは未知のキーを無視すべきである。
