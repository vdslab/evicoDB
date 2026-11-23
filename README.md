# evicoDB

本リポジトリは、病理診断に関する文献情報・診断情報・検査所見を
正規化して管理するための MySQL データベース定義と、
教科書のテキストから所見を LLM（Gemini）で抽出するツール（annotator）をまとめたものです。

元データは Excel 形式の表で、1行に診断・検査・文献情報が混在していましたが、
本データベースではそれを以下の3テーブルに分離しています。

- diagnoses: 診断マスタ
- findings: 検査・所見データ
- bibliography: 文献情報

Docker を用いることで、誰でも同一環境でデータベースを再現できます。


## データ構造概要

### diagnoses
診断そのものを表すマスタテーブルです。

- 診断名
- ICD-Oコード
- 臓器
- 原発 / 転移
- 良悪性
など、診断に固有の情報を保持します。

1つの診断に対して、複数の所見（findings）が紐づきます。
「臓器 + 診断名」の組み合わせは重複できません。


### findings
各検査・所見を表すテーブルです。

Excel の 1 行、または LLM で抽出して人が確認した所見 1 件に相当します。

- 検査方法（IHC / Genetic test など）
- 分子・マーカー名
- 検査結果（元の記述と、陽性 / 陰性 / 遺伝子異常あり / 判定困難 の判定）
- 画像ファイル名
- 参照文献

診断（diagnoses）および文献（bibliography）への外部キーを持ちます。
出典が存在しない場合は reference_id を NULL とします。

DB には人が確認済みの所見だけを登録します。LLM による抽出結果の確認は DB の外（annotator/output）で行います。


### bibliography
文献情報を管理するテーブルです。

- 論文情報（雑誌名、巻、ページ、年など）
- URL（PubMed 等）

複数の所見から同一文献を参照できる構造になっています。


## テーブル定義

テーブル定義は以下の SQL ファイルに記載されています。

- mysql/init/schema.sql

Docker コンテナ初回起動時に自動で実行されます。
スキーマを変更した場合は、データを削除して作り直す必要があります（`docker compose down -v && docker compose up -d`）。

ER 図は docs/er-diagram.md にあります。



## 環境構築方法（Docker）

### 必要なもの
- Docker
- Docker Compose

### 起動方法

```bash
docker compose up -d
```


### MySQL への接続

```bash
docker exec -it evico-mysql mysql -u evico -p
```

パスワードは docker-compose.yml に記載されています。


## 出典が存在しないデータについて

一部の所見データには、元資料に出典が存在しない場合があります。

その場合は、

- findings.reference_id を NULL
- bibliography にはレコードを作成しない

という形で管理します。


## annotator（LLM による所見の自動抽出）

WHO 腫瘍分類（Thoracic Tumours, 第 5 版）のテキストから、Gemini で「診断 × 分子 × 結果」を抽出し、
人が確認してから DB に登録するためのツールです（`annotator/`）。

```
annotator/input/*.txt
  → extract.py（抽出）     → annotator/output/<日時>_<プロンプト名>/*.json
  → evaluate.py（評価）    → 正解データ（肺・消化器）と比べた Precision・抽出率・F1
  → review.py（確認用一覧） → review.csv
  → 人が確認 → DB に登録
```

| スクリプト | 役割 |
| --- | --- |
| `extract.py` | テキストから診断と所見を抽出し、実行ごとのフォルダに保存する |
| `evaluate.py` | 正解データと比べて、診断ごとに Precision・抽出率（Recall）・F1 を出す |
| `review.py` | 抽出した所見を、間違えやすそうな順に並べた一覧（review.csv）を作る |
| `clean_labels.py` | 正解データ（Excel）の誤字・表記ゆれを直した修正版を作る |

### 準備

1. Python の依存関係を入れる（`rye sync`、または `pip install -r annotator/requirements.txt`）
2. `annotator/.env` に `GCP_PROJECT`・`GCP_LOCATION`・`GEMINI_MODEL` を設定する
3. Vertex AI を使える Google アカウントの認証情報を `GOOGLE_APPLICATION_CREDENTIALS` で指定する

`annotator/input/`・`annotator/label/`・`annotator/output/` は `.gitignore` の対象で、リポジトリには含まれません。

### 料金の目安

いちばん大きいのは Gemini の API 料金です。Cloud Run・GCS・Artifact Registry の費用は、それに比べてごくわずかです（2026-11-23 時点）。

| 抽出の範囲 | ファイル数 | 1 回 | 3 回（多数決） |
| --- | ---: | ---: | ---: |
| 肺の章（実測） | 54 | 約 2.3 ドル（2.0〜2.7） | 約 7 ドル |
| 胸部の全章（肺・胸膜・心臓・間葉系・縦隔） | 151 | 約 5.5 ドル（4.8〜6.5） | 約 16 ドル |
| 消化器の 4 章（食道・胃・大腸・肝臓） | 42 | 約 2.0 ドル（1.7〜2.4） | 約 6 ドル |

prompt v6.2、`gemini-3.8-flash`（`GCP_LOCATION=global`）での目安です。プロンプトやモデルを変えると変わります。

#### 根拠

**Gemini の単価**：Cloud Billing Catalog API（`cloudbilling.googleapis.com`、サービス `Vertex AI`）の SKU から。

| SKU | 単価 |
| --- | ---: |
| Gemini 3.8 Flash Global Text Input - Predictions | 100 万トークンあたり 1.50 ドル |
| Gemini 3.8 Flash Global Text Output - Predictions | 100 万トークンあたり 7.50 ドル |

思考（thinking）のトークンは出力として課金されます。extract.py は、入力・出力・思考のトークン数を結果の JSON に記録しています（`extraction_runs.token_usage`。出力の `candidates_token_count` に思考は含まれません）。

**肺の章の実測**：prompt v6.2 の 4 回分（2026-10-23 の 1 回、10-27 の seed 1/2/3）の `token_usage` の合計から。

| | 1 回あたり |
| --- | ---: |
| 入力 | 333,385 トークン（4 回とも同じ） → 0.50 ドル |
| 出力 | 約 10 万トークン → 約 0.76 ドル |
| 思考 | 10.1 万〜19.6 万トークン（回によって大きく変わる） → 0.76〜1.47 ドル |
| 合計 | 2.02〜2.73 ドル |

料金の半分以上は出力と思考で、思考の量が回ごとに変わるため幅があります。

**ほかの章の見積もり**：肺の章の 6 回分（324 ファイル）から、テキストの大きさ（バイト数）あたりのトークン数を出し、各章のテキストの大きさに掛けました。

- 入力：1 ファイルあたり 4,372 トークン（プロンプトの固定部分）＋ 1 バイトあたり 0.231 トークン（最小二乗法）
- 出力：1 バイトあたり 0.241 トークン
- 思考：1 バイトあたり 0.328 トークン（範囲は 0.240〜0.466 で、表の幅はこれから）

肺以外の章で、所見の書かれ方の密度が肺と違えば、実際の料金もずれます。最初に回したときに `token_usage` で確かめてください。

**Cloud Run Jobs**（asia-northeast1）：CPU 1 秒あたり 0.000018 ドル、メモリ 1 GiB・1 秒あたり 0.000002 ドル。ジョブは 1 vCPU・512 MiB なので、1 タスク 1 秒あたり 0.000019 ドルです。手元の docker では、肺の章 1 回がタスクの合計で約 30 分 → 約 0.03 ドル。胸部の全章でも 1 回 0.1 ドルほどです。

**GCS**（Tokyo、Standard）：1 GB・1 か月あたり 0.023 ドル。入力と出力は合わせて数 MB なので、ほぼ 0 です。

**Artifact Registry**：0.5 GB まで無料、超えた分は 1 GB・1 か月あたり 0.10 ドル。

#### 料金を抑えるには

- 失敗したファイルだけ回し直すときは `--resume` を付ける（終わったファイルは飛ばす）
- 3 回まわして多数決にすると 3 倍かかる。プロンプトを試すときは 1 回、本番の結果を作るときだけ 3 回にする
- 急がない処理なら、Gemini の Batch 予測は入力・出力とも半額（`Gemini 3.8 Flash Global Text Input/Output - Batch Predictions`：100 万トークンあたり 0.75 / 3.75 ドル）。ただし extract.py は今は対応していない

### 詳しい説明

- 実行コマンド：docs/commands.md
- 入力テキスト・正解データ・対応表・評価のルール：docs/annotator-data.md
