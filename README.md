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
  → evaluate.py（評価）    → 正解データ（肺）と比べた Precision・抽出率・F1
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

### 詳しい説明

- 実行コマンド：docs/commands.md
- 入力テキスト・正解データ・対応表・評価のルール：docs/annotator-data.md
