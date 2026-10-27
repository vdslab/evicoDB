# Cloud Run Jobs で抽出を回す

annotator/extract.py をコンテナにして Cloud Run Jobs で動かす。入力テキストと出力は GCS のバケットに置き、ジョブからは `/mnt/data` にマウントして読み書きする。

- 1 回の実行（execution）が 1 つの run になる。ファイルはタスクに分けて並列に処理する（タスク i は i 番目から `タスク数` おきのファイルを担当）
- API の呼び出しにはタイムアウト（既定 600 秒）があり、429 や 5xx は SDK が指数バックオフで再試行する
- `--resume` を付けると、出力がすでにあるファイルは飛ばす。そのため、タスクが失敗して再試行されても続きから処理する
- 生成の設定は temperature 0 と `--seed`（既定 1）で固定する。多数決（consensus.py）用に 3 回まわすときは、seed を 1/2/3 に変える

## 変数

```bash
PROJECT=vdslab-students
REGION=asia-northeast1
BUCKET=vdslab-students-evico          # 管理者に作ってもらったバケット
REPO=evico                            # Artifact Registry のリポジトリ
IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/annotator
SA=annotator-runner@$PROJECT.iam.gserviceaccount.com
JOB=annotator-extract
```

## 入力をアップロード

```bash
# 1 項目 1 ファイルのテキスト。章ごとにフォルダを分ける（例：input/lung/）
# input/lung/ はシンボリックリンクなので --no-ignore-symlinks で実体をアップロードする（既定ではリンクは飛ばされる）
gcloud storage rsync -r --no-ignore-symlinks annotator/input gs://$BUCKET/input
```

## イメージを作って push

```bash
gcloud auth configure-docker $REGION-docker.pkg.dev
# Cloud Run は amd64 で動くので、Apple Silicon の Mac ではプラットフォームを指定する
docker build --platform linux/amd64 -t $IMAGE annotator
docker push $IMAGE
```

## ジョブを作る（作り直すときも同じコマンド）

```bash
gcloud run jobs deploy $JOB \
  --project $PROJECT --region $REGION \
  --image $IMAGE \
  --service-account $SA \
  --set-env-vars GCP_PROJECT=$PROJECT,GCP_LOCATION=global,GEMINI_MODEL=gemini-3.8-flash \
  --add-volume name=data,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount volume=data,mount-path=/mnt/data \
  --tasks 6 --parallelism 3 \
  --task-timeout 3h --max-retries 3 \
  --cpu 1 --memory 512Mi
```

- `--parallelism` は、同時に API を呼ぶタスクの数。1 分あたりのトークン上限（TPM）に引っかからない数にする。手元で 3 本並列にしたときは 429 が出た。今は SDK が再試行するので止まりはしないが、429 が多いときは数を減らす
- `--tasks` は、ファイルを何組に分けるか。肺の章（54 ファイル）を 6 組に分けると、1 組あたり 9 ファイル（約 5 分）になる

## 実行する

ジョブを作ったあとは、実行するたびに引数（`--args`）で run の名前・入力・seed を指定する。

```bash
RUNS=()
for SEED in 1 2 3; do
  # run の名前は手元の run と同じ <timestamp>_<prompt>（stability.py が先頭を日時として読む）。seed は JSON に記録される
  RUN=$(date +%Y%m%d-%H%M%S)_prompt-v6.2; RUNS+=($RUN)
  gcloud run jobs execute $JOB --project $PROJECT --region $REGION --async \
    --args="--input-dir=/mnt/data/input/lung,--run-id=$RUN,--seed=$SEED,--resume"
  sleep 1
done
echo $RUNS
```

- `--args` は Dockerfile の ENTRYPOINT のあとに付け足される。`--input-dir` は後に書いた方が使われる
- 3 本を同時に実行すると、並列の数は 3 × `--parallelism` になる。TPM が足りないときは 1 本ずつ実行する
- 進み具合：`gcloud run jobs executions list --job $JOB --region $REGION`。ログは Cloud Logging（コンソールの実行の画面から見られる）
- 失敗したファイルがあると、そのタスクは終了コード 1 で終わり、`--max-retries` の回数まで再試行される（`--resume` を付けているので、終わったファイルは飛ばす）

## 結果を手元に落として評価する

```bash
for RUN in $RUNS; do
  gcloud storage rsync -r gs://$BUCKET/output/$RUN annotator/output/$RUN
done
python annotator/consensus.py annotator/output/${^RUNS}
python annotator/evaluate.py --output-dir annotator/output/<consensus.py が作ったフォルダ>
```

## ローカルの docker で動かす（確認用）

`annotator/input/lung/` の中身は `annotator/input/` を指す絶対パスのシンボリックリンクなので、リンク先も同じパスでマウントする（GCS へは `--no-ignore-symlinks` を付けて実体をアップロードする）。

```bash
docker build -t annotator annotator
docker run --rm \
  -v "$PWD/annotator/input:/mnt/data/input:ro" \
  -v "$PWD/annotator/input:$PWD/annotator/input:ro" \
  -v "$PWD/annotator/output:/mnt/data/output" \
  -v "$HOME/.config/gcloud-adc/university.json:/adc.json:ro" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
  -e GCP_PROJECT=vdslab-students -e GCP_LOCATION=global -e GEMINI_MODEL=gemini-3.8-flash \
  -e CLOUD_RUN_TASK_INDEX=0 -e CLOUD_RUN_TASK_COUNT=2 \
  annotator --input-dir=/mnt/data/input/lung --run-id=local-test --resume
```

## 管理者にお願いすること

`vdslab-students` では、今は Cloud Run と Artifact Registry の API が無効で、GCS も使えない。次のように依頼する。

> evico の抽出（Gemini）を Cloud Run Jobs で回したいので、vdslab-students で次の設定をお願いできますか。
>
> 1. API の有効化：Cloud Run Admin API（run.googleapis.com）、Artifact Registry API（artifactregistry.googleapis.com）
> 2. Artifact Registry に Docker リポジトリ `evico`（asia-northeast1）を作成
> 3. GCS バケット `vdslab-students-evico`（asia-northeast1）を作成
> 4. ジョブ用のサービスアカウント `annotator-runner` を作成し、次のロールを付与
>    - プロジェクト：Vertex AI ユーザー（roles/aiplatform.user）
>    - バケット `vdslab-students-evico`：Storage オブジェクト管理者（roles/storage.objectAdmin）
> 5. 私のアカウント（chko23054@g.nihon-u.ac.jp）に次のロールを付与
>    - プロジェクト：Cloud Run デベロッパー（roles/run.developer）
>    - リポジトリ `evico`：Artifact Registry 書き込み（roles/artifactregistry.writer）
>    - サービスアカウント `annotator-runner`：サービスアカウント ユーザー（roles/iam.serviceAccountUser）
>    - バケット `vdslab-students-evico`：Storage オブジェクト管理者（roles/storage.objectAdmin）
>
> 費用は、肺の章 1 回で約 1 ドル（Gemini の API 料金）です。Cloud Run と GCS の費用はごくわずかの見込みです。
