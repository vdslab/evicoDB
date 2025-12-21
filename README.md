# evicoDB

本リポジトリは、病理診断に関する文献情報・診断情報・検査所見を
正規化して管理するための MySQL データベース定義をまとめたものです。

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


### findings
各検査・所見を表すテーブルです。

Excelの1行に相当します。

- 検査方法（IHC / Genetic test など）
- 分子・マーカー名
- 検査結果
- 画像ファイル名
- 参照文献

診断（diagnoses）および文献（bibliography）への外部キーを持ちます。
出典が存在しない場合は reference_id を NULL とします。


### bibliography
文献情報を管理するテーブルです。

- 論文情報（雑誌名、巻、ページ、年など）
- URL（PubMed 等）

複数の所見から同一文献を参照できる構造になっています。


## テーブル定義

テーブル定義は以下の SQL ファイルに記載されています。

- mysql/init/schema.sql

Docker コンテナ初回起動時に自動で実行されます。



## 環境構築方法（Docker）

### 必要なもの
- Docker
- Docker Compose

### 起動方法

```

docker compose up -d

```


### MySQL への接続

```

docker exec -it evico-mysql mysql -u evico -p

```

パスワードは docker-compose.yml に記載されています。


## 出典が存在しないデータについて

一部の所見データには、元資料に出典が存在しない場合があります。

その場合は、

- findings.reference_id を NULL
- bibliography にはレコードを作成しない

という形で管理します。