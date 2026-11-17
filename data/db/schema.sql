-- 城市房价预测与朝向溢价分析 —— SQLite 建表脚本
-- 用于把 raw JSONL / clean.csv / features.csv / 模型运行记录导入数据库。
-- 用法：sqlite3 data/db/house.db < data/db/schema.sql

PRAGMA journal_mode = WAL;

-- 原始层：一条房源一行，主键 (source, url)
CREATE TABLE IF NOT EXISTS raw_listings (
    source        TEXT NOT NULL,
    source_id     TEXT,
    city          TEXT,
    city_code     TEXT,
    district      TEXT,
    plate         TEXT,
    community     TEXT,
    title         TEXT,
    url           TEXT NOT NULL,
    layout        TEXT,
    rooms         INTEGER,
    halls         INTEGER,
    bathrooms     INTEGER,
    area          REAL,
    orientation_raw TEXT,
    orientation   TEXT,
    floor_raw     TEXT,
    floor_level   TEXT,
    total_floors  INTEGER,
    build_year    INTEGER,
    decoration    TEXT,
    elevator      TEXT,
    building_type TEXT,
    total_price   REAL,
    unit_price    REAL,
    unit_price_calc REAL,
    unit_price_mismatch INTEGER,
    page          INTEGER,
    list_url      TEXT,
    crawl_time    TEXT,
    PRIMARY KEY (source, url)
);
CREATE INDEX IF NOT EXISTS idx_raw_city ON raw_listings(city_code);
CREATE INDEX IF NOT EXISTS idx_raw_district ON raw_listings(district);

-- 清洗层：W2 输出，异常/别墅只标记不删除
CREATE TABLE IF NOT EXISTS clean_listings (
    row_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,
    city_code         TEXT,
    city              TEXT,
    district          TEXT,
    district_source   TEXT,
    plate             TEXT,
    community         TEXT,
    title             TEXT,
    url               TEXT NOT NULL UNIQUE,
    layout            TEXT,
    rooms             INTEGER,
    halls             INTEGER,
    bathrooms         INTEGER,
    area              REAL,
    orientation       TEXT,
    orientation_main  TEXT,
    is_south          INTEGER,
    is_south_north    INTEGER,
    orientation_count INTEGER,
    orientation_missing INTEGER,
    floor_level       TEXT,
    total_floors      INTEGER,
    floor_ratio       REAL,
    build_year        INTEGER,
    age               INTEGER,
    age_missing       INTEGER,
    decoration        TEXT,
    elevator          TEXT,
    building_type     TEXT,
    is_villa          INTEGER DEFAULT 0,
    total_price       REAL,
    unit_price        REAL,
    unit_price_raw    REAL,
    unit_price_suspect INTEGER,
    is_outlier        INTEGER DEFAULT 0,
    outlier_reason    TEXT,
    cross_source_dup  INTEGER DEFAULT 0,
    dup_of_url        TEXT,
    model_keep        INTEGER DEFAULT 1,
    clean_version     TEXT
);
CREATE INDEX IF NOT EXISTS idx_clean_city ON clean_listings(city_code);
CREATE INDEX IF NOT EXISTS idx_clean_source ON clean_listings(source);

-- 特征层：W3 输出，带训练/测试划分
CREATE TABLE IF NOT EXISTS features (
    row_id        INTEGER PRIMARY KEY,
    district      TEXT,
    community     TEXT,
    split         TEXT CHECK(split IN ('train', 'test')),
    unit_price    REAL,
    log_unit_price REAL,
    features_json TEXT,
    feature_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_features_split ON features(split);

-- 模型运行记录：便于复现
CREATE TABLE IF NOT EXISTS model_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_config    TEXT,
    model_name    TEXT,
    metrics_json  TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
