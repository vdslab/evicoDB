# Annotator のデータ構成

`annotator/input/`（抽出元テキスト）と `annotator/label/`（正解データ）の中身についてまとめます。
どちらも `.gitignore` 対象のため、リポジトリには含まれていません。

## input/: 抽出元テキスト

WHO 腫瘍分類（Thoracic Tumours, 第5版）の本文を、**1診断 = 1ファイル**に分割したテキストです。

### ファイル名

`<開始ページ番号>_<章の見出し>.txt`

例: `041_Sclerosing pneumocytoma.txt` は 41 ページから始まる Sclerosing pneumocytoma の項目です。

各ファイルは WHO の定型見出しの順に並んでいます。

```
Sclerosing pneumocytoma
Definition / ICD-O coding / Related terminology / Subtype(s) / Localization / ...
```

分子マーカー（IHC・遺伝子検査）の記述は、主に Immunohistochemistry や Diagnostic molecular pathology の節にあります。

### 章ごとの内訳

| ページ | 章 | ファイル数 | 正解データ |
|---|---|---|---|
| 029–189 | 肺 | 54 | あり（48件が対応） |
| 194–224 | 胸膜（中皮腫など） | 9 | なし |
| 228–269 | 心臓 | 20 | なし |
| 274–448 | 縦隔（胸腺腫・胚細胞腫瘍・リンパ腫など） | 68 | なし |

### 抽出・評価の対象外とするファイル

| ファイル | 理由 |
|---|---|
| `127_Lung neuroendocrine neoplasms Introduction` など `Introduction` を含むもの | 章の総論で、個別の診断ではない |
| `029_Small diagnostic samples` | 生検検体の扱いに関する章 |
| `References.txt` | 文献リスト（約714KB、input 全体の約4割）。抽出にかけるとトークンを浪費する |
| `example.txt` | `075_Invasive mucinous adenocarcinoma` のテスト用コピー |
| `lung/` | 肺の54ファイルのコピー（下記参照） |

### lung/ フォルダ

肺の章（029–189）の54ファイルだけを集めたフォルダです。正解データがあるのは肺だけなので、評価用の抽出はこちらを指定します。

```bash
python annotator/extract.py --input-dir annotator/input/lung
```

## label/: 正解データ

### example-label.csv

医学部の先生方が手作業で作成した正解データです（3,017行）。1行が「診断 × 分子 × 結果」の1組に対応します。

評価で使う主な列:

| 列 | 内容 |
|---|---|
| `Organs` | 臓器 |
| `Diagnosis` | 診断名 |
| `Methods` | 検査方法（IHC など） |
| `Molecules` | 分子・マーカー名 |
| `Results` | 検査結果 |

収録臓器は Soft tissue, Ovary, Lung, Uterus, Colon, Kidney, Uterine cervix, Urinary tract, Stomach, Prostate の10種類です。
input の章のうち正解データがあるのは **Lung（422行・83診断）のみ**です。

注意点:

- 診断名に誤字があります（例: `Squamous cell carcinma`, `lnvasive mucinous adenocarcinoma`, `Leqidic adenocarcinoma`）。
- `Results` の表記は393通りあります（`Positive`, `Negative` のほか `Positive, maybe`, `Both`, `Various results` など）。

このファイルは生データとしてそのまま残し、評価には下記の修正版を使います。

### example-label-clean.csv / example-label-changes.csv

`annotator/clean_labels.py` が生データから作る修正版と、その変更履歴です。

```bash
python annotator/clean_labels.py
```

| ファイル | 内容 |
|---|---|
| `example-label-clean.csv` | 修正版。evaluate.py の既定の正解データ |
| `example-label-changes.csv` | 変更したセルの一覧（行番号・列・修正前・修正後） |

修正するのは評価・DB 投入に使う列（`Organs`, `Primary/Metastasis`, `Origin`, `Malignancy`, `Major classifications`, `Diagnosis`, `Diagnosis (EN)`, `Methods`, `Molecules`, `Results`）だけです。

| 修正内容 | 例 |
|---|---|
| 明らかなつづりの誤り | `carcinma` → `carcinoma`, `Mutaiton` → `Mutation`, `Leqidic` → `Lepidic` |
| 前後の空白・連続する空白 | `Epitherial ` → `Epithelial` |
| 全角記号を半角に | `（`, `）`, `，`, `、` |
| 選択肢の表記の統一 | `malignant` → `Malignant`, `Primary tumor` → `Primary`, `Chromosomal test` → `Chromosome test` |

意図的に修正していないもの:

- 英米のつづりの違い（`tumor` / `tumour`, `leukemia` / `leukaemia`, `hemangioma` / `haemangioma`）
- 誤字に見えるが正しい用語（`Prostein`, `SALL4`, `angioendothelioma`, `SCNC` など）
- `References`, `Books`, `URL`, `Photos` 列

修正する単語は `clean_labels.py` の `TYPOS` に一覧があります。誤字を見つけたらここに追加して再実行します。

### diagnosis-mapping.csv

教科書（WHO 第5版）の診断と、正解データの `Diagnosis` の対応表です。
input/lung の各ファイルの ICD-O coding の節に並んでいる1行（コードと診断名）を1つの診断として、1行ずつ対応させています。
正解データは「1ファイル = 1診断」で作られたものではないため、1つのファイルに複数の診断が含まれることも、診断が含まれないこと（総論）もあります。

| 列 | 内容 |
|---|---|
| `input_file` | input/lung のファイル名 |
| `icd_o` | ICD-O コード（ICD-O coding の節がないファイルは空） |
| `who_diagnosis` | 教科書の診断名（ICD-O coding の行の名前） |
| `label_diagnoses` | 対応する正解データの診断名（複数ある場合は ` ; ` 区切り） |
| `status` | 対応の種類（下表） |
| `note` | 補足 |

| status | 件数 | 意味 |
|---|---|---|
| `exact` | 58 | 1対1で対応（誤字・旧名を含む） |
| `merged` | 2 | 第4版の複数の亜型を、第5版のまとめた診断に対応（089 Squamous cell carcinoma, NOS、103 Pleomorphic carcinoma） |
| `check` | 5 | 対応が不確か。先生方に確認予定 |
| `none` | 9 | 対応する正解データなし（教科書にしかない診断、総論のファイル） |

`check` の5件:

- `Invasive non-mucinous adenocarcinoma` → 正解データの総称の `Adenocarcinoma` に対応させてよいか
- `Mild / Moderate / Severe squamous dysplasia` → 正解データは `Dysplasia` 1つ（程度の区別なし）
- `PEComa, benign` → `Clear cell tumour`（良性 PEComa の旧名）も含めるか

evaluate.py は、この表の1行（診断）ごとに採点します（prompt v3 以降の出力が対象）。
ある診断の所見は、`applies_to` にその診断名がある所見と、すべての診断に共通の所見（`applies_to` が空）です。
この表にないファイルは採点しません。

正解データの Lung 診断のうち、以下の14件は input/lung に対応する章がありません。

Breast carcinoma, Colorectal adenocarcinoma, Prostatic adenocarcinoma, Urothelial bladder cancer（以上、転移性腫瘍）, Epithelioid haemangioendothelioma, Germ cell tumor, Inflammatory myofibroblastic tumor, Intrapulmonary thymoma, Merkel cell carcinoma, NUT carcinoma, Synovial sarcoma, Teratoma (mature / immature), Thymic carcinoma

### result-mapping.csv

`Results` の表記（Lung の55通り）と抽出側の `result_normalized`（6通り）を、採点用の判定にまとめる対応表です。先生方の確認用の下書きです。

| 列 | 内容 |
|---|---|
| `source` | `label`（正解データの表記）/ `extraction`（抽出側の表記） |
| `result_raw` | 元の表記 |
| `count_lung` | Lung の正解データでの出現行数 |
| `category_proposed` | 判定の案（下表） |
| `modifier` | 判定以外の補足（細胞・成分 / 範囲・強さ / 局在 / 確からしさ低 / 混在 / 条件付き） |
| `current_mapping` | 現在の evaluate.py での判定 |
| `needs_review` | 判断が難しく先生方に確認したいもの |
| `note` | 確認したい点 |
| `teacher_check` | 先生方の確認結果を記入する欄 |

| 判定 | 意味 |
|---|---|
| `Positive` | 陽性 |
| `Negative` | 陰性 |
| `Altered` | 遺伝子異常あり（Fusion, Amplification など） |
| `Equivocal` | 陽性・陰性を決めにくい（低発現、陽性と陰性の混在など） |
| `Exclude` | 採点から除外（`No recommendation` の行など） |

## molecule-aliases.csv: 分子名の別名

`annotator/molecule-aliases.csv`（git 管理）は、同じ分子の別の書き方をまとめた表です。evaluate.py はこの表で分子名をそろえてから照合します。

| 列 | 内容 |
|---|---|
| `canonical` | 代表名 |
| `aliases` | 別名（`;` 区切り） |

例: `ER` = `estrogen receptor`、`PgR` = `PR`、`Keratins` = `pancytokeratin` / `AE1/AE3`

表にない分子でも、正解データの `ER(Estrogen receptor)` のような括弧付きの名前は、括弧の外（`ER`）と中（`Estrogen receptor`）のどちらでも照合します。
照合漏れを見つけたら、この表に1行追加します。

## 評価のルール（evaluate.py）

分子ごとに、正解データと抽出結果の判定（Positive / Negative / Altered / Equivocal）を比べます。

| Status | 条件 | 数え方 |
|---|---|---|
| `TP (Match)` | 両方にあり、判定が一致 | TP |
| `Mismatch` | 両方にあり、判定が不一致 | FP と FN |
| `FN (Missing)` | 正解データにあり、本文にも出てくるが抽出されなかった | FN |
| `Not in GT (excluded)` | 抽出されたが正解データにない | 数えない |
| `Not in text (excluded)` | 正解データにあるが本文に出てこない（第4版由来など） | 数えない |

`Altered`（遺伝子異常あり）と `Positive` は一致とみなします。正解データでは遺伝子異常の多くが `Positive` と書かれているためです（例: EGFR mutation → Positive）。

Recall を「抽出率」（正解のうち抽出できた割合）として表示します。

評価結果は実行フォルダに保存されます。

| ファイル | 内容 |
|---|---|
| `evaluation_report.md` | 画面に表示されるレポート |
| `evaluation_details.csv` | ファイル × 分子ごとの比較結果（間違い方の分析用） |
