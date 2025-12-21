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
CREATE TABLE
    diagnoses (
        diagnosis_id INT NOT NULL COMMENT '診断ID（Excel由来のID、手動管理）',
        diagnosis TEXT NULL COMMENT '診断名（例: Invasive mucinous adenocarcinoma）',
        icd_o TEXT NULL COMMENT 'ICD-Oコード',
        major_classifications TEXT NULL COMMENT '大分類（例: Adenocarcinoma）',
        organs TEXT NULL COMMENT '臓器（例: Lung）',
        primary_metastasis TEXT NULL COMMENT '原発/転移（Primary / Metastasis）',
        origin TEXT NULL COMMENT '組織学的起源（例: Epithelial）',
        malignancy TEXT NULL COMMENT '良悪性区分（benign / malignant など）',
        PRIMARY KEY (diagnosis_id)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '診断マスタ';

-- 所見テーブル
-- Excelの1行＝1所見に相当
-- 診断（diagnoses）と文献（bibliography）を結びつける事実テーブル
CREATE TABLE
    findings (
        finding_id INT NOT NULL AUTO_INCREMENT COMMENT '所見ID（自動採番）',
        diagnosis_id INT NOT NULL COMMENT '対応する診断ID（diagnoses.diagnosis_id）',
        reference_id INT NULL COMMENT '参照文献ID（bibliography.bibliography_id）',
        method TEXT NULL COMMENT '検査方法（IHC / Genetic test など）',
        molecule_name TEXT NULL COMMENT '分子・マーカー名（例: TTF-1, CK7, KRAS mutation）',
        molecule_description TEXT NULL COMMENT '分子の説明・補足情報',
        result TEXT NULL COMMENT '検査結果（例: Positive, Negative, Positive, focal など）',
        photo TEXT NULL COMMENT '画像ファイル名またはパス',
        PRIMARY KEY (finding_id),
        KEY idx_findings_diagnosis_id (diagnosis_id),
        KEY idx_findings_reference_id (reference_id),
        CONSTRAINT fk_findings_diagnosis FOREIGN KEY (diagnosis_id) REFERENCES diagnoses (diagnosis_id) ON UPDATE CASCADE ON DELETE RESTRICT,
        CONSTRAINT fk_findings_reference FOREIGN KEY (reference_id) REFERENCES bibliography (bibliography_id) ON UPDATE CASCADE ON DELETE SET NULL
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '診断ごとの検査・所見情報';