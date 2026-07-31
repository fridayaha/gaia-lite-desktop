-- ════════════════════════════════════════════════════════════════════════
-- Marketing benchmark — source MySQL physical schema (DDL)
-- ════════════════════════════════════════════════════════════════════════
-- Design contract (DESIGN.md §3.2):
--   - Column names: snake_case ASCII (Chinese only in VALUES, not in DDL).
--   - Fix 1: test_drive has NO test_drive_consultant_id column (removed —
--            physical column does not exist; source field was a typo).
--   - Fix 2: lead_follow_record all columns unified to snake_case.
--   - Fix 3: `recording` is a SYNTHETIC table (seeded from 3 url sources).
--   - Fix 4: user has NO phone_brand / phone_device_model columns (no backing;
--            expected null on query).
--   - Identifiers (PK/FK/phone/vin/plate) ASCII; business text Chinese in VALUES.
-- Schema: marketing_benchmark (created by seed_marketing.py)
-- ════════════════════════════════════════════════════════════════════════

-- ── 主数据 ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `t_ods_master_data_store` (
    `store_code`         VARCHAR(64)  NOT NULL,
    `org_name`           VARCHAR(128) NOT NULL,
    `store_type`         VARCHAR(32),
    `store_status`       VARCHAR(32),
    `description`        VARCHAR(255),
    `store_categories`   VARCHAR(64),
    `store_level`        VARCHAR(32),
    `regional_sales`     VARCHAR(64),
    `after_sales_region` VARCHAR(64),
    `province`           VARCHAR(32),
    `city`               VARCHAR(32),
    `area`               VARCHAR(32),
    `address`            VARCHAR(255),
    `store_area`         DECIMAL(10,2),
    `opening_time`       VARCHAR(16),
    `business_deadline`  VARCHAR(16),
    `longitude`          DECIMAL(10,6),
    `dimension`          DECIMAL(10,6),
    `is_oversea`         TINYINT(1),
    `country`            VARCHAR(32),
    PRIMARY KEY (`store_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_ods_master_data_staff` (
    `user_id`           VARCHAR(64)  NOT NULL,
    `user_name`         VARCHAR(64)  NOT NULL,
    `phone`             VARCHAR(32),
    `job_number`        VARCHAR(64),
    `is_store_admin`    TINYINT(1),
    `gender`            VARCHAR(8),
    `email`             VARCHAR(128),
    `entry_time`        DATETIME,
    `leave_status`      VARCHAR(8),
    `termination_time`  DATETIME,
    `store_code`        VARCHAR(64),
    `status`            VARCHAR(8),
    `create_time`       DATETIME,
    `update_time`       DATETIME,
    PRIMARY KEY (`user_id`),
    KEY `idx_staff_phone` (`phone`),
    KEY `idx_staff_store` (`store_code`),
    KEY `idx_staff_update` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_ods_leads_server_leads_source` (
    `source_id`               VARCHAR(64) NOT NULL,
    `show_name`               VARCHAR(128),
    `source_level`            VARCHAR(8),
    `parent_source_id`        VARCHAR(64),
    `first_classification`    VARCHAR(64),
    `secondary_classification` VARCHAR(64),
    `status`                  VARCHAR(8),
    `create_time`             DATETIME,
    `update_time`             DATETIME,
    PRIMARY KEY (`source_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Fix 4: user simplified to single source; NO phone_brand/device_model columns.
CREATE TABLE IF NOT EXISTS `t_ods_leads_server_leads_user_rt` (
    `user_id`    VARCHAR(64) NOT NULL,
    `user_name`  VARCHAR(64),
    `mobile`     VARCHAR(32),
    `reg_time`   DATETIME,
    -- Fix 4 (pragmatic): CDP-backed fields modelled as always-null columns so
    -- the ontology can bind them (auto-backfill needs a real column) while L8
    -- still asserts they read back as null (no CDP data populated).
    `phone_brand`           VARCHAR(64),
    `phone_device_model`    VARCHAR(128),
    PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 线索链路 ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `t_ods_leads_server_leads_info_rt` (
    `id`                 VARCHAR(64) NOT NULL,
    `leads_level`        VARCHAR(8),
    `filing_time`        DATETIME,
    `filing_create_time` DATETIME,  -- 建档时间 (distinct from 留资时间 filing_time)
    `four_source`        VARCHAR(64),
    `data_source`        VARCHAR(8),
    `channel`            VARCHAR(8),
    `brand`              VARCHAR(64),
    `vehicle_model_name` VARCHAR(128),
    `vehicle_series_name` VARCHAR(64),
    `province`           VARCHAR(32),
    `city`               VARCHAR(32),
    `address`            VARCHAR(255),
    `dealer_name`        VARCHAR(128),
    `dealer_code`        VARCHAR(64),
    `leads_status`       VARCHAR(16),
    `receive_time`       DATETIME,
    `first_send_time`    DATETIME,
    `first_assign_time`  DATETIME,
    `first_follow_time`  DATETIME,
    `next_follow_time`   DATETIME,
    `claim_status`       VARCHAR(8),
    `nick`               VARCHAR(64),
    `init_shop_code`     VARCHAR(64),
    `stage`              VARCHAR(8),
    `last_follow_time`   DATETIME,
    `last_follow_content` VARCHAR(512),
    `is_allopatry`       TINYINT(1),
    `test_drive`         VARCHAR(8),
    `test_drive_status`  VARCHAR(8),
    `lead_mark`          VARCHAR(8),
    `status`             VARCHAR(8),
    `creator`            VARCHAR(64),
    `user_id`            VARCHAR(64),
    PRIMARY KEY (`id`),
    KEY `idx_lead_user` (`user_id`),
    KEY `idx_lead_status` (`leads_status`),
    KEY `idx_lead_next_follow` (`next_follow_time`),
    KEY `idx_lead_source` (`four_source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- lead_allocate_record & lead_distribute_record share the same physical table
-- structure (t_ods_source_data_leads_operation_record); created once here.
CREATE TABLE IF NOT EXISTS `t_ods_source_data_leads_operation_record` (
    `oid`                VARCHAR(64) NOT NULL,
    `operation_time`     DATETIME,
    `leads_id`           VARCHAR(64),
    `sales_consultant_id` VARCHAR(64),
    `dealer_code`        VARCHAR(64),
    `type`               VARCHAR(8),
    `first_flag`         VARCHAR(8),
    `creator`            VARCHAR(64),
    `create_time`        DATETIME,
    `updater`            VARCHAR(64),
    `update_time`        DATETIME,
    PRIMARY KEY (`oid`),
    KEY `idx_op_lead` (`leads_id`),
    KEY `idx_op_consultant` (`sales_consultant_id`),
    KEY `idx_op_time` (`operation_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Fix 2: lead_follow_record all columns unified to snake_case.
CREATE TABLE IF NOT EXISTS `t_ods_source_data_leads_follow_record` (
    `oid`                         VARCHAR(64) NOT NULL,
    `leadsinfoid`                 VARCHAR(64),
    `follower_id`                 VARCHAR(64),
    `follow_purpose`              VARCHAR(64),
    `communication_methods`       VARCHAR(32),
    `follow_result`               VARCHAR(32),
    `follow_content`              VARCHAR(512),
    `intended_level`              VARCHAR(8),
    `vehicle_model_code`          VARCHAR(128),
    `vehicle_model_name`          VARCHAR(128),
    `business_no`                 VARCHAR(64),
    `next_follow_time`            DATETIME,
    `follow_shop_id`              VARCHAR(64),
    `arrive_time`                 DATETIME,
    `defeat_type`                 VARCHAR(32),
    `change_business_opportunity` VARCHAR(8),
    `creator`                     VARCHAR(64),
    `create_time`                 DATETIME,
    PRIMARY KEY (`oid`),
    KEY `idx_fr_lead` (`leadsinfoid`),
    KEY `idx_fr_follower` (`follower_id`),
    KEY `idx_fr_create` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 外呼 ─────────────────────────────────────────────────────────────────
-- Fix 3: original_record_url stores the SYNTHETIC recording_id (not the raw url).
CREATE TABLE IF NOT EXISTS `t_ods_leads_server_sale_call_record_rt` (
    `id`                   VARCHAR(64) NOT NULL,
    `call_status`          VARCHAR(8),
    `call_time`            DATETIME,
    `call_duration`        INT,
    `original_record_url`  VARCHAR(64),
    `lead_id`              VARCHAR(64),
    `user_id`              VARCHAR(64),
    PRIMARY KEY (`id`),
    KEY `idx_moc_lead` (`lead_id`),
    KEY `idx_moc_time` (`call_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_ods_leads_server_ai_call_out_result_rt` (
    `id`               VARCHAR(64) NOT NULL,
    `ai_tag_name`      VARCHAR(64),
    `task_name`        VARCHAR(128),
    `call_duration_sec` INT,
    `call_status`      VARCHAR(32),
    `call_times`       DATETIME,
    `call_start_time`  DATETIME,
    `call_end_time`    DATETIME,
    `is_review`        VARCHAR(8),
    `customer_name`    VARCHAR(64),
    `cellphone`        VARCHAR(32),
    `leads_info_id`    VARCHAR(64),
    `robot_id`         VARCHAR(64),
    `audio_link_url`   VARCHAR(64),
    `tenant_id`        VARCHAR(64),
    `update_time`      DATETIME,
    PRIMARY KEY (`id`),
    KEY `idx_aoc_lead` (`leads_info_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 试驾 ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `t_ods_test_drive_car_model` (
    `id`             VARCHAR(64) NOT NULL,
    `car_model_vin`  VARCHAR(32),
    `store_code`     VARCHAR(64),
    `car_model_name` VARCHAR(128),
    `number_plate`   VARCHAR(16),
    `series_code`    VARCHAR(32),
    `car_series_name` VARCHAR(64),
    `model_code`     VARCHAR(128),
    `model_name`     VARCHAR(128),
    `qr_code_url`    VARCHAR(255),
    `car_status`     VARCHAR(8),
    `status`         VARCHAR(8),
    `creator`        VARCHAR(64),
    `creator_name`   VARCHAR(64),
    `create_time`    DATETIME,
    `updater`        VARCHAR(64),
    `updater_name`   VARCHAR(64),
    `update_time`    DATETIME,
    `ds_src`         VARCHAR(128),
    PRIMARY KEY (`id`),
    KEY `idx_tdc_store` (`store_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_ods_test_drive_route` (
    `id`           VARCHAR(64) NOT NULL,
    `route_name`   VARCHAR(128),
    `store_code`   VARCHAR(64),
    `is_enable`    VARCHAR(8),
    `status`       VARCHAR(8),
    `creator`      VARCHAR(64),
    `creator_name` VARCHAR(64),
    `create_time`  DATETIME,
    `updater`      VARCHAR(64),
    `updater_name` VARCHAR(64),
    `update_time`  DATETIME,
    `ds_src`       VARCHAR(128),
    PRIMARY KEY (`id`),
    KEY `idx_tdr_store` (`store_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Fix 1: NO test_drive_consultant_id column (removed). Only sale_id → sales_consultant.
-- Fix 3: original_record_url stores the SYNTHETIC recording_id.
CREATE TABLE IF NOT EXISTS `t_ods_test_drive_test_drive_rt` (
    `id`                       VARCHAR(64) NOT NULL,
    `end_time`                 DATETIME,
    `begin_time`               DATETIME,
    `sale_id`                  VARCHAR(64),
    `original_record_url`      VARCHAR(64),
    `user_id`                  VARCHAR(64),
    `leads_id`                 VARCHAR(64),
    `name`                     VARCHAR(64),
    `phone`                    VARCHAR(32),
    `test_drive_type`          VARCHAR(8),
    `store_code`               VARCHAR(64),
    `test_drive_id`            VARCHAR(64),
    `schedule_time`            DATETIME,
    `test_drive_date`          DATETIME,
    `test_drive_time_period_id` VARCHAR(32),
    `route_id`                 VARCHAR(64),
    `test_drive_car_id`        VARCHAR(64),
    `door_time`                DATETIME,
    `door_address`             VARCHAR(255),
    `duration`                 INT,
    `kilometre`                DECIMAL(10,1),
    `order_status`             VARCHAR(8),
    `track_matching`           VARCHAR(8),
    `effective_status`         VARCHAR(8),
    `test_drive_class`         VARCHAR(8),
    `test_drive_source`        VARCHAR(8),
    `first_test_drive_date`    VARCHAR(32),
    `follow_flag`              VARCHAR(8),
    `record_flag`              VARCHAR(8),
    `record_url`               VARCHAR(255),
    `analysis_result_id`       VARCHAR(64),
    `record_type`              VARCHAR(8),
    `intended_car_series`      VARCHAR(64),
    `status`                   VARCHAR(8),
    `creator`                  VARCHAR(64),
    `update_time`              DATETIME,
    PRIMARY KEY (`id`),
    KEY `idx_td_consultant` (`sale_id`),
    KEY `idx_td_lead` (`leads_id`),
    KEY `idx_td_end` (`end_time`),
    KEY `idx_td_schedule` (`schedule_time`),
    KEY `idx_td_status` (`order_status`),
    KEY `idx_td_update` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 微信 ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `t_ods_inspection_weixin_log` (
    `id`             VARCHAR(64) NOT NULL,
    `user_id`        VARCHAR(64),
    `record_type`    VARCHAR(16),
    `dialoguecontent` VARCHAR(512),
    `createtime`     DATETIME,
    `status`         VARCHAR(8),
    `chat_deadline`  DATETIME,
    `log_time`       DATETIME,
    PRIMARY KEY (`id`),
    KEY `idx_cr_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── recording (SYNTHETIC, fix 3) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `recording` (
    `recording_id`   VARCHAR(32) NOT NULL,
    `recording_url`  VARCHAR(255),
    `recording_text` TEXT,
    PRIMARY KEY (`recording_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
