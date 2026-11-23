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
BUCKET=vdslab-evico                   # 入力と出力を置くバケット（プロジェクト vdslab-207906 にある）
REPO=evico                            # Artifact Registry のリポジトリ
IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/annotator
SA=annotator-runner@$PROJECT.iam.gserviceaccount.com
JOB=annotator-extract
```

## 入力をアップロード

```bash
# 1 項目 1 ファイルのテキスト。章ごとにフォルダを分ける（例：input/lung/）
# input/lung/ などの章のフォルダはシンボリックリンクなので --no-ignore-symlinks で実体をアップロードする（既定ではリンクは飛ばされる）
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

初めて動かすとき・ジョブを作り直したときは、いきなり全章・3 本を回さずに、まず肺の章を seed 1 の 1 本だけ回す。GCS のマウント、Gemini の呼び出し、ログの閲覧、結果の取得（下の「結果を手元に落として評価する」）まで通ることを確かめてから、本番の実行に進む。

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

```bash
docker build -t annotator annotator
docker run --rm \
  -v "$PWD/annotator/input:/mnt/data/input:ro" \
  -v "$PWD/annotator/output:/mnt/data/output" \
  -v "$HOME/.config/gcloud-adc/university.json:/adc.json:ro" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
  -e GCP_PROJECT=vdslab-students -e GCP_LOCATION=global -e GEMINI_MODEL=gemini-3.8-flash \
  -e CLOUD_RUN_TASK_INDEX=0 -e CLOUD_RUN_TASK_COUNT=2 \
  annotator --input-dir=/mnt/data/input/lung --run-id=local-test --resume
```

## 準備済みの環境（2026-11-21 に管理者が構築）

API の有効化、リポジトリ・バケット・サービスアカウントの作成、権限の付与は、すべて管理者（先生）が済ませている。自分で準備する作業はない。

権限は、プロジェクトの編集者（`roles/editor`）ではなく、Cloud Run Jobs の実行に必要な分だけを付けている。そのため、コンソールでバケットの作成画面や一部の設定画面を開くと権限不足と出ることがあるが、このページの CLI の操作・ジョブの実行・ログの閲覧には影響しない。

| もの | 場所 | 権限 |
| --- | --- | --- |
| API | `vdslab-students` | `run.googleapis.com`、`artifactregistry.googleapis.com` を有効化済み |
| 自分のアカウント（`chko23054@g.nihon-u.ac.jp`） | `vdslab-students` | カスタムロール `CloudRunBatchDeveloper`（ジョブの作成・更新・実行（`--args` の上書きを含む）・キャンセル・削除、ログの閲覧）、`VertexAI`（Gemini の呼び出し。前から付いているもの） |
| Artifact Registry のリポジトリ `evico` | `vdslab-students`（`asia-northeast1`） | 自分に `roles/artifactregistry.writer` |
| サービスアカウント `annotator-runner` | `vdslab-students` | SA に `roles/aiplatform.user`。自分に、この SA の `roles/iam.serviceAccountUser` |
| GCS バケット `vdslab-evico` | **`vdslab-207906`**（`asia-northeast1`、uniform bucket-level access・パブリックアクセス防止あり） | 自分と SA に `roles/storage.objectUser` |

- バケットは `vdslab-students` ではなく `vdslab-207906` にある。`vdslab-students` のコンソールのバケット一覧には出ないが、`gcloud storage` でもジョブのマウント（`--add-volume … bucket=vdslab-evico`）でも、プロジェクトをまたいでそのまま読み書きできる
- 権限不足のエラーが出たら、実行したコマンドとエラーメッセージを先生に伝える

### 疎通確認

大学のアカウントでログインした状態で、次がエラーにならないことを確かめる。

```bash
gcloud auth list                                                                       # 大学のアカウントに * が付いている
gcloud storage ls gs://$BUCKET
gcloud artifacts repositories describe $REPO --project $PROJECT --location $REGION
gcloud run jobs list --project $PROJECT --region $REGION
```

### 費用

肺の章 1 回で約 2.3 ドル（2.0〜2.7 ドル、ほぼ Gemini の API 料金）、胸部の全章で 1 回約 5.5 ドル。Cloud Run と GCS の費用はごくわずか。根拠と章ごとの見積もりは README.md の「料金の目安」。
