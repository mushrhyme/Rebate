-- 条件請求書パースシステム PostgreSQLスキーマ
-- シンプルな2テーブル構造: パースセッション + 項目データ
-- ヒストリー管理: パースセッション概念で複数回の再パース結果を比較可能

-- ============================================
-- 1. パースセッションテーブル (parsing_sessions) - ヒストリー管理用
-- ============================================
CREATE TABLE parsing_sessions (
    session_id SERIAL PRIMARY KEY,                    -- パースセッション固有ID
    pdf_filename VARCHAR(500) NOT NULL,              -- PDFファイル名 (同一ファイルの複数パースを識別)
    session_name VARCHAR(255),                        -- セッション名 (例: "初回パース", "プロンプト改善後"など)
    is_latest BOOLEAN DEFAULT FALSE,                 -- 最新セッションかどうか (ユーザー向け表示用)
    parsing_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- パース実行日時
    notes TEXT,                                       -- メモ (開発者用: 何を変更したかなど)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP   -- 作成日時
);

-- ============================================
-- 2. 項目テーブル (items) - すべてのデータを含む
-- ============================================
CREATE TABLE items (
    item_id SERIAL PRIMARY KEY,                       -- 項目固有ID
    session_id INTEGER NOT NULL REFERENCES parsing_sessions(session_id) ON DELETE CASCADE, -- パースセッションID (FK)
    
    -- ユーザー必須情報 (エクセル出力用)
    management_id VARCHAR(255),                       -- 管理番号
    customer VARCHAR(255),                            -- 取引先
    product_name VARCHAR(500),                        -- 商品名
    quantity INTEGER,                                 -- 数量 (最終計算済み数量、計算可能な場合のみ)
    case_count INTEGER,                               -- ケース数 (ケース単位の数量、ない場合はnull)
    bara_count INTEGER,                               -- バラ数 (バラ単位の数量、ない場合はnull)
    units_per_case INTEGER,                           -- ケース内入数 (1ケースあたりの個数、ない場合はnull)
    amount BIGINT,                                    -- 金額 (整数、円単位)
    page_number INTEGER NOT NULL,                    -- ページ番号 (1から開始、ユーザー用)
    page_role VARCHAR(50),                            -- ページ役割 (cover/main/detail/reply)
    
    -- 文書メタデータ (重複保存、検索用)
    issuer VARCHAR(255),                               -- 発行者 (例: "ヤマエ久野株式会社")
    issue_date VARCHAR(100),                          -- 発行日 (例: "2024年06月06日")
    billing_period VARCHAR(255),                      -- 請求期間 (例: "2024年05月分")
    total_amount_document BIGINT,                     -- 文書全体総額 (整数、円単位)
    pdf_filename VARCHAR(500),                        -- PDFファイル名 (検索用)
    
    -- 開発者用
    page_index INTEGER,                               -- ページインデックス (0から開始、開発者用)
    item_order INTEGER,                               -- 項目順序
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP   -- 作成日時
);

-- ============================================
-- 2-1. ページ画像テーブル (page_images) - 画像をBYTEAで保存
-- ============================================
CREATE TABLE page_images (
    image_id SERIAL PRIMARY KEY,                      -- 画像固有ID
    session_id INTEGER NOT NULL REFERENCES parsing_sessions(session_id) ON DELETE CASCADE, -- パースセッションID (FK)
    page_number INTEGER NOT NULL,                    -- ページ番号 (1から開始)
    image_data BYTEA NOT NULL,                       -- 画像データ (PNG形式、BYTEA)
    image_format VARCHAR(10) DEFAULT 'PNG',          -- 画像形式 (PNG, JPEG等)
    image_size INTEGER,                               -- 画像サイズ (バイト)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 作成日時
    UNIQUE(session_id, page_number)                  -- 同一セッション内でページ番号は一意
);

-- ============================================
-- 3. インデックス作成 (パフォーマンス最適化)
-- ============================================
-- パースセッション検索用インデックス
CREATE INDEX idx_parsing_sessions_pdf_filename ON parsing_sessions(pdf_filename);
CREATE INDEX idx_parsing_sessions_is_latest ON parsing_sessions(pdf_filename, is_latest);
CREATE INDEX idx_parsing_sessions_timestamp ON parsing_sessions(parsing_timestamp);

-- 項目検索用インデックス
CREATE INDEX idx_items_session_id ON items(session_id);
CREATE INDEX idx_items_management_id ON items(management_id);
CREATE INDEX idx_items_customer ON items(customer);
CREATE INDEX idx_items_product_name ON items(product_name);
CREATE INDEX idx_items_page_number ON items(page_number);
CREATE INDEX idx_items_pdf_filename ON items(pdf_filename);
CREATE INDEX idx_items_issuer ON items(issuer);
CREATE INDEX idx_items_issue_date ON items(issue_date);
CREATE INDEX idx_items_billing_period ON items(billing_period);

-- ページ画像検索用インデックス
CREATE INDEX idx_page_images_session_id ON page_images(session_id);
CREATE INDEX idx_page_images_page_number ON page_images(session_id, page_number);

-- ============================================
-- 4. ビュー: ユーザー向けエクセル出力用 (最新セッションのみ)
-- ============================================
CREATE VIEW user_excel_view AS
SELECT 
    -- ユーザー必須情報 (エクセル列順序)
    management_id AS 管理番号,                        -- 管理番号
    customer AS 取引先,                              -- 取引先
    product_name AS 商品名,                            -- 商品名
    quantity AS 数量,                                 -- 数量 (最終計算済み数量、計算可能な場合のみ)
    case_count AS ケース数,                          -- ケース数
    bara_count AS バラ数,                            -- バラ数
    units_per_case AS ケース内入数,                  -- ケース内入数
    amount AS 金額,                                   -- 請求金額
    page_number AS ページ番号,                       -- ページ番号 (1から開始)
    page_role AS ページ役割,                          -- ページ役割
    
    -- 追加コンテキスト情報 (必要時に使用)
    issuer AS 発行者,                                 -- 発行者
    issue_date AS 発行日,                             -- 発行日
    billing_period AS 請求期間,                       -- 請求期間
    pdf_filename AS PDFファイル名,                    -- PDFファイル名
    
    -- 開発者用ID (非表示可能)
    item_id,
    session_id
FROM items
WHERE session_id IN (
    SELECT session_id FROM parsing_sessions WHERE is_latest = TRUE
)
ORDER BY pdf_filename, page_number, item_order;

-- ============================================
-- 5. ビュー: 開発者向けセッション比較用 (複数セッションを比較可能)
-- ============================================
CREATE VIEW developer_session_comparison AS
SELECT 
    ps.session_id,
    ps.session_name,
    ps.parsing_timestamp,
    ps.is_latest,
    ps.pdf_filename,
    ps.notes,
    COUNT(DISTINCT i.item_id) AS total_items,
    COUNT(DISTINCT i.management_id) AS total_management_ids,
    COUNT(DISTINCT i.page_number) AS total_pages,
    SUM(i.amount) AS calculated_total_amount,
    AVG(i.amount) AS avg_item_amount,
    MIN(i.created_at) AS first_item_created,
    MAX(i.created_at) AS last_item_created
FROM parsing_sessions ps
LEFT JOIN items i ON ps.session_id = i.session_id
GROUP BY ps.session_id, ps.session_name, ps.parsing_timestamp, ps.is_latest, ps.pdf_filename, ps.notes
ORDER BY ps.pdf_filename, ps.parsing_timestamp DESC;
