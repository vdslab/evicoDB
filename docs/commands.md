# よく使うコマンド集

## MySQL (Docker)

```bash
# 起動
docker compose up -d

# 停止
docker compose down

# 停止 + データも削除（初期化したい時）
docker compose down -v

# コンテナに入って mysql クライアントで接続
docker exec -it evico-mysql mysql -u evico -p
# パスワード: docker-compose.yml の MYSQL_PASSWORD 参照（evico_pass）

# root で入る場合
docker exec -it evico-mysql mysql -u root -p
# パスワード: MYSQL_ROOT_PASSWORD（root）

# スキーマを再適用したい場合（ボリュームごと作り直し）
docker compose down -v && docker compose up -d
```

よく使う SQL:

```sql
USE evicoDB;
SHOW TABLES;
SELECT * FROM diagnoses LIMIT 10;
SELECT * FROM findings LIMIT 10;
SELECT * FROM bibliography LIMIT 10;
```

## Python 環境 (Rye)

```bash
# 依存関係を同期（初回・pyproject.toml 変更時）
rye sync

# パッケージ追加（pip install は使わない）
rye add <package_name>

# annotator/ 配下は独自の requirements.txt もあるので pip でも可
pip install -r annotator/requirements.txt
```

## Annotator: 抽出 (extract.py)

`annotator/.env` に `GCP_PROJECT` / `GCP_LOCATION` / `GEMINI_MODEL` を設定しておく。
今は `GEMINI_MODEL=gemini-3.8-flash` を使っている（未設定のときも gemini-3.8-flash になる）。

Vertex AI を使える Google アカウントの認証情報を指定してから実行する。

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/gcloud-adc/university.json"
```

プロンプトの既定は最新の `prompt-v4.txt`（確信度つき）。前の版を使うときは `--prompt` で指定する。

```bash
# annotator/input/lung/*.txt（正解データがある肺の章）を抽出 → annotator/output/<日時>_<プロンプト名>/*.json
# 例: annotator/output/20261004-195300_prompt-v2/
# API エラーは1ファイルにつき3回まで再試行し、それでも失敗したファイルは飛ばして最後に一覧表示する
python annotator/extract.py

# 1ファイルだけ抽出（失敗したファイルのやり直しなど）
python annotator/extract.py --input-dir "annotator/input/lung/089_Squamous cell carcinoma.txt"

# 前の版のプロンプト（v3）で抽出
python annotator/extract.py --prompt annotator/prompts/prompt-v3.txt

# 入出力ディレクトリ・プロンプトを指定
python annotator/extract.py \
  --prompt annotator/prompts/prompt-v4.txt \
  --input-dir annotator/input/lung \
  --output-dir annotator/output
```

## Annotator: 評価 (evaluate.py)

```bash
# 正解データの誤字を直した修正版を作る（example-label.csv を更新したら再実行）
python annotator/clean_labels.py

# 最新の実行フォルダの全 JSON を label/example-label-clean.csv と突き合わせて精度算出
python annotator/evaluate.py

# 過去の実行フォルダを指定して評価（prompt-v3 以降の出力のみ対応）
python annotator/evaluate.py --output-dir annotator/output/20261005-033733_prompt-v4

# 1ファイルだけ評価したい場合
python annotator/evaluate.py --single-json "annotator/output/20261005-033733_prompt-v4/089_Squamous cell carcinoma.json"
```

評価結果は実行フォルダに `evaluation_report.md` と `evaluation_details.csv` として保存される。

## Annotator: 人が確認する一覧 (review.py)

```bash
# 最新の実行フォルダから review.csv を作る（間違えやすそうな順に並ぶ）
python annotator/review.py

# 実行フォルダを指定する場合
python annotator/review.py --output-dir annotator/output/20261005-033733_prompt-v4
```

## Git

```bash
git status
git log --oneline -10
```
