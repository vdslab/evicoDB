-- 文献情報テーブル
-- Excelの「References」「URL」に対応
-- 複数の所見（findings）から参照される
CREATE TABLE
    bibliography (
        bibliography_id INT NOT NULL AUTO_INCREMENT COMMENT '文献ID（自動採番）',
        bibliography_text TEXT NOT NULL COMMENT '文献情報（雑誌名・巻・ページ・年など）',
        bibliography_url TEXT NULL COMMENT '文献のURL（PubMedなど）',
        PRIMARY KEY (bibliography_id)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '文献マスタ（Reference情報）';

-- 診断マスタテーブル
-- Excelの「Organs」「Primary/Metastasis」「Origin」
-- 「Malignancy」「Major classifications」「Diagnosis」「ICD-O」に対応
-- 1診断に対して複数の所見（findings）が紐づく
-- 上位分類と下位分類は parent_diagnosis_id で親子にする
--   例: Adenocarcinoma ＞ Invasive non-mucinous adenocarcinoma ＞ Lepidic adenocarcinoma
CREATE TABLE
    diagnoses (
        diagnosis_id INT NOT NULL AUTO_INCREMENT COMMENT '診断ID（自動採番）',
        diagnosis VARCHAR(255) NOT NULL COMMENT '診断名（例: Invasive mucinous adenocarcinoma）。旧名は登録しない',
        parent_diagnosis_id INT NULL COMMENT '上位分類の診断ID（diagnoses.diagnosis_id）。最上位なら NULL',
        organs VARCHAR(100) NOT NULL COMMENT '臓器（例: Lung）',
        icd_o VARCHAR(20) NULL COMMENT 'ICD-Oコード（例: 8253/3）',
        major_classifications VARCHAR(255) NULL COMMENT '大分類（例: Adenocarcinoma）',
        primary_metastasis VARCHAR(50) NULL COMMENT '原発/転移（Primary / Metastasis）',
        origin VARCHAR(50) NULL COMMENT '組織学的起源（例: Epithelial）',
        malignancy VARCHAR(50) NULL COMMENT '良悪性区分（Benign / Malignant など）',
        PRIMARY KEY (diagnosis_id),
        UNIQUE KEY uq_diagnoses_organ_diagnosis (organs, diagnosis),
        KEY idx_diagnoses_parent_diagnosis_id (parent_diagnosis_id),
        CONSTRAINT fk_diagnoses_parent FOREIGN KEY (parent_diagnosis_id) REFERENCES diagnoses (diagnosis_id) ON UPDATE CASCADE ON DELETE RESTRICT
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '診断マスタ';

-- 所見テーブル
-- Excelの1行＝1所見に相当
-- 診断（diagnoses）と文献（bibliography）を結びつける事実テーブル
CREATE TABLE
    findings (
        finding_id INT NOT NULL AUTO_INCREMENT COMMENT '所見ID（自動採番）',
        diagnosis_id INT NOT NULL COMMENT '対応する診断ID（diagnoses.diagnosis_id）',
        reference_id INT NULL COMMENT '参照文献ID（bibliography.bibliography_id）',
        method VARCHAR(100) NULL COMMENT '検査方法（IHC / Genetic test など）',
        molecule_name VARCHAR(255) NULL COMMENT '分子・マーカー名（例: TTF-1, CK7, KRAS mutation）',
        molecule_description TEXT NULL COMMENT '分子の説明・補足情報',
        result TEXT NULL COMMENT '検査結果の記述（例: Positive, Negative, Positive, focal など）',
        result_category ENUM('Positive', 'Negative', 'Altered') NULL COMMENT '検査結果の判定（陽性 / 陰性 / 遺伝子異常あり）',
        qualifiers JSON NULL COMMENT '判定の注釈（例: ["ほぼ", "条件付き:細胞"]）。100% そうとは言えないことを表す',
        qualifier_note TEXT NULL COMMENT '注釈の中身（例: stromal cells, 40-80%）',
        evidence_text TEXT NULL COMMENT '根拠となる本文（原文のまま）',
        source VARCHAR(100) NULL COMMENT '所見の出どころ（例: Excel, prompt-v6.2）',
        photo TEXT NULL COMMENT '画像ファイル名またはパス',
        PRIMARY KEY (finding_id),
        KEY idx_findings_diagnosis_id (diagnosis_id),
        KEY idx_findings_reference_id (reference_id),
        CONSTRAINT fk_findings_diagnosis FOREIGN KEY (diagnosis_id) REFERENCES diagnoses (diagnosis_id) ON UPDATE CASCADE ON DELETE RESTRICT,
        CONSTRAINT fk_findings_reference FOREIGN KEY (reference_id) REFERENCES bibliography (bibliography_id) ON UPDATE CASCADE ON DELETE SET NULL
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '診断ごとの検査・所見情報';
