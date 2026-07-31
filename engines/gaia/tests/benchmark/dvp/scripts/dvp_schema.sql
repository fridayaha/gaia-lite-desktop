-- DVP Benchmark — MySQL source schema (fixture seeding target)
--
-- This is the SOURCE-SIDE MySQL schema for the DVP benchmark. The fixture
-- seeder (seed_dvp.py) writes here directly (DESIGN.md §5.1, seeding
-- exception). Gaia registers these tables as VIRTUAL datasets (no Iceberg/
-- Doris); Trino federates to them at query time.
--
-- Naming contract (DESIGN.md §3.3):
--   - All columns snake_case ASCII (preserves word boundaries).
--   - Identifiers (PK/FK/code) ASCII; business text (names/descriptions)
--     Chinese values inserted by the seeder.
--   - PK/FK columns are explicit; FK columns are plain columns (no real FK
--     constraints — seeder guarantees referential integrity in topological
--     order, and Trino federation doesn't need them).
--   - Shared table t_oper_condition_detail carries a `condition_type`
--     discriminator column (front_collision/rear_collision/side_collision/
--     pedestrian_protect); 4 ObjectTypes share it (DESIGN.md §3.4 修正4).
--
-- Database: dvp_benchmark (created by seeder with --drop).

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ═══════════════════════════════════════════════════════════════════════════
-- Domain 1: 项目与目标 (Project & Target)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS t_project_base (
    project_code        VARCHAR(32)  NOT NULL,
    project_name        VARCHAR(128) NOT NULL,
    brand               VARCHAR(64)  NOT NULL,
    project_type        VARCHAR(32)  NOT NULL,
    dev_tier            VARCHAR(32)  NOT NULL,
    lifecycle_state     VARCHAR(32)  NOT NULL,
    project_status      VARCHAR(8)   NOT NULL,
    manager_name        VARCHAR(64)  NOT NULL,
    research_unit       VARCHAR(128) NOT NULL,
    project_start_time  DATE         NULL,
    plan_end_time       DATE         NULL,
    approval_date       DATE         NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    create_by           VARCHAR(64)  NOT NULL,
    update_by           VARCHAR(64)  NOT NULL,
    delete_mark         TINYINT(1)   NOT NULL DEFAULT 0,
    PRIMARY KEY (project_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_project_vehicle (
    vehicle_code        VARCHAR(32)  NOT NULL,
    project_code        VARCHAR(32)  NOT NULL,
    vehicle_name        VARCHAR(128) NOT NULL,
    power_type          VARCHAR(32)  NOT NULL,
    drive_type          VARCHAR(32)  NOT NULL,
    dev_tier            VARCHAR(32)  NOT NULL,
    target_market       VARCHAR(64)  NOT NULL,
    vehicle_category    VARCHAR(32)  NOT NULL,
    development_method  VARCHAR(32)  NOT NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    create_by           VARCHAR(64)  NOT NULL,
    update_by           VARCHAR(64)  NOT NULL,
    PRIMARY KEY (vehicle_code),
    KEY idx_pv_project (project_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_lms_project (
    lms_project_id      VARCHAR(32)  NOT NULL,
    project_code        VARCHAR(32)  NOT NULL,
    bom_id              VARCHAR(64)  NULL,
    sample_car_sys_id   VARCHAR(64)  NULL,
    lms_project_name    VARCHAR(128) NOT NULL,
    sync_status         VARCHAR(16)  NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    PRIMARY KEY (lms_project_id),
    KEY idx_lp_project (project_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_project_target (
    target_code         VARCHAR(32)  NOT NULL,
    project_code        VARCHAR(32)  NOT NULL,
    target_title        VARCHAR(128) NOT NULL,
    target_category     VARCHAR(32)  NOT NULL,
    target_description  VARCHAR(512) NULL,
    target_response_dept VARCHAR(128) NOT NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    create_by           VARCHAR(64)  NOT NULL,
    PRIMARY KEY (target_code),
    KEY idx_pt_project (project_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_lms_target_dimension (
    dimension_id        VARCHAR(32)  NOT NULL,
    target_code         VARCHAR(32)  NOT NULL,
    dimension_title     VARCHAR(128) NOT NULL,
    dimension_category  VARCHAR(32)  NOT NULL,
    target_threshold    VARCHAR(64)  NULL,
    response_unit       VARCHAR(128) NOT NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    PRIMARY KEY (dimension_id),
    KEY idx_ltd_target (target_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_lms_target_iteration (
    iteration_id        VARCHAR(32)  NOT NULL,
    dimension_id        VARCHAR(32)  NOT NULL,
    iteration_version   VARCHAR(16)  NOT NULL,
    iteration_date      DATE         NOT NULL,
    iteration_threshold VARCHAR(64)  NULL,
    change_note         VARCHAR(512) NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    PRIMARY KEY (iteration_id),
    KEY idx_lti_dim (dimension_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ═══════════════════════════════════════════════════════════════════════════
-- Domain 2: 车辆物理结构 (Vehicle Physical Structure)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS t_vehicle_body (
    body_code           VARCHAR(32)  NOT NULL,
    vehicle_code        VARCHAR(32)  NOT NULL,
    body_name           VARCHAR(128) NOT NULL,
    vehicle_weight      DOUBLE       NULL,
    body_form           VARCHAR(32)  NOT NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    PRIMARY KEY (body_code),
    KEY idx_vb_vehicle (vehicle_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Generic structure table builder would be ideal; MySQL has no templating,
-- so we write the 4 structure tables + exterior explicitly. They share the
-- same shape: <x>_structure_code / body_code / <x>_structure_name / extras.

CREATE TABLE IF NOT EXISTS t_front_structure (
    front_structure_code VARCHAR(32)  NOT NULL,
    body_code            VARCHAR(32)  NOT NULL,
    front_structure_name VARCHAR(128) NOT NULL,
    front_rail_form      VARCHAR(32)  NULL,
    energy_box_type      VARCHAR(32)  NULL,
    status               VARCHAR(8)   NOT NULL,
    create_time          DATETIME     NOT NULL,
    update_time          DATETIME     NOT NULL,
    PRIMARY KEY (front_structure_code),
    KEY idx_fs_body (body_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_side_structure (
    side_structure_code VARCHAR(32)  NOT NULL,
    body_code           VARCHAR(32)  NOT NULL,
    side_structure_name VARCHAR(128) NOT NULL,
    b_pillar_form       VARCHAR(32)  NULL,
    sill_form           VARCHAR(32)  NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    PRIMARY KEY (side_structure_code),
    KEY idx_ss_body (body_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_rear_structure (
    rear_structure_code VARCHAR(32)  NOT NULL,
    body_code           VARCHAR(32)  NOT NULL,
    rear_structure_name VARCHAR(128) NOT NULL,
    rear_rail_form      VARCHAR(32)  NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    PRIMARY KEY (rear_structure_code),
    KEY idx_rs_body (body_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_chassis_structure (
    chassis_structure_code VARCHAR(32)  NOT NULL,
    body_code              VARCHAR(32)  NOT NULL,
    chassis_structure_name VARCHAR(128) NOT NULL,
    suspension_form        VARCHAR(32)  NULL,
    steering_form          VARCHAR(32)  NULL,
    status                 VARCHAR(8)   NOT NULL,
    create_time            DATETIME     NOT NULL,
    update_time            DATETIME     NOT NULL,
    PRIMARY KEY (chassis_structure_code),
    KEY idx_cs_body (body_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_exterior_design (
    exterior_code   VARCHAR(32)  NOT NULL,
    body_code       VARCHAR(32)  NOT NULL,
    exterior_name   VARCHAR(128) NOT NULL,
    exterior_type   VARCHAR(32)  NOT NULL,
    stiffness_param VARCHAR(64)  NULL,
    status          VARCHAR(8)   NOT NULL,
    create_time     DATETIME     NOT NULL,
    update_time     DATETIME     NOT NULL,
    PRIMARY KEY (exterior_code),
    KEY idx_ed_body (body_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- component.structure_code points at one of the 5 structure tables (front/
-- side/rear/chassis/exterior); structure_type disambiguates which.
CREATE TABLE IF NOT EXISTS t_component (
    component_id        VARCHAR(32)  NOT NULL,
    structure_code      VARCHAR(32)  NOT NULL,
    structure_type      VARCHAR(16)  NOT NULL,
    component_name      VARCHAR(128) NOT NULL,
    component_category  VARCHAR(64)  NOT NULL,
    spec_model          VARCHAR(64)  NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    create_by           VARCHAR(64)  NOT NULL,
    PRIMARY KEY (component_id),
    KEY idx_comp_struct (structure_type, structure_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_change_point_entity (
    change_point_id     VARCHAR(32)  NOT NULL,
    component_id        VARCHAR(32)  NOT NULL,
    change_description  VARCHAR(256) NOT NULL,
    change_degree       INT          NOT NULL,
    weight              DOUBLE       NULL,
    change_type         VARCHAR(32)  NOT NULL,
    status              VARCHAR(8)   NOT NULL,
    create_time         DATETIME     NOT NULL,
    update_time         DATETIME     NOT NULL,
    create_by           VARCHAR(64)  NOT NULL,
    PRIMARY KEY (change_point_id),
    KEY idx_cpe_comp (component_id),
    KEY idx_cpe_degree (change_degree)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ═══════════════════════════════════════════════════════════════════════════
-- Domain 3: 试验与验证 (Test & Verification)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS t_oper_condition (
    condition_code        VARCHAR(32)  NOT NULL,
    condition_name        VARCHAR(128) NOT NULL,
    condition_description VARCHAR(512) NULL,
    condition_category    VARCHAR(32)  NOT NULL,
    status                VARCHAR(8)   NOT NULL,
    create_time           DATETIME     NOT NULL,
    update_time           DATETIME     NOT NULL,
    PRIMARY KEY (condition_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- SHARED by 4 ObjectTypes (FrontCollision/RearCollision/SideCollision/
-- PedestrianProtect). condition_type discriminates rows. Each OT registers
-- the SAME physical table as a VIRTUAL dataset but filters condition_type at
-- query time (DESIGN.md §3.4 修正4, L7/L13 回归覆盖).
CREATE TABLE IF NOT EXISTS t_oper_condition_detail (
    detail_condition_code VARCHAR(32)  NOT NULL,
    condition_code        VARCHAR(32)  NOT NULL,
    condition_type        VARCHAR(24)  NOT NULL,
    detail_condition_name VARCHAR(128) NOT NULL,
    test_description      VARCHAR(512) NULL,
    status                VARCHAR(8)   NOT NULL,
    create_time           DATETIME     NOT NULL,
    update_time           DATETIME     NOT NULL,
    PRIMARY KEY (detail_condition_code),
    KEY idx_ocd_cond (condition_code),
    KEY idx_ocd_type (condition_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_test_item (
    test_item_id          VARCHAR(32)  NOT NULL,
    detail_condition_code VARCHAR(32)  NOT NULL,
    dimension_id          VARCHAR(32)  NULL,
    spec_code             VARCHAR(32)  NULL,
    test_item_name        VARCHAR(128) NOT NULL,
    sample_count          INT          NULL,
    evaluation_criteria   VARCHAR(256) NULL,
    prep_period           INT          NULL,
    test_response         VARCHAR(64)  NOT NULL,
    status                VARCHAR(8)   NOT NULL,
    plan_end_time         DATE         NULL,
    create_time           DATETIME     NOT NULL,
    update_time           DATETIME     NOT NULL,
    create_by             VARCHAR(64)  NOT NULL,
    PRIMARY KEY (test_item_id),
    KEY idx_ti_detail (detail_condition_code),
    KEY idx_ti_dim (dimension_id),
    KEY idx_ti_spec (spec_code),
    KEY idx_ti_status (status),
    KEY idx_ti_create (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_spec (
    spec_code             VARCHAR(32)  NOT NULL,
    spec_name             VARCHAR(128) NOT NULL,
    applicable_model      VARCHAR(64)  NULL,
    test_preparation      VARCHAR(512) NULL,
    operation_steps       VARCHAR(512) NULL,
    equipment_requirement VARCHAR(256) NULL,
    pass_threshold        VARCHAR(64)  NULL,
    status                VARCHAR(8)   NOT NULL,
    create_time           DATETIME     NOT NULL,
    update_time           DATETIME     NOT NULL,
    create_by             VARCHAR(64)  NOT NULL,
    PRIMARY KEY (spec_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_lms_trial_standard (
    standard_id     VARCHAR(32)  NOT NULL,
    spec_code       VARCHAR(32)  NOT NULL,
    standard_name   VARCHAR(128) NOT NULL,
    test_cost       DOUBLE       NULL,
    total_cost      DOUBLE       NULL,
    external_quote  DOUBLE       NULL,
    sample_count    INT          NULL,
    work_hours      INT          NULL,
    equipment_list  VARCHAR(256) NULL,
    test_period     INT          NULL,
    status          VARCHAR(8)   NOT NULL,
    create_time     DATETIME     NOT NULL,
    update_time     DATETIME     NOT NULL,
    PRIMARY KEY (standard_id),
    KEY idx_lts_spec (spec_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_dvp_design (
    dvp_code           VARCHAR(32)  NOT NULL,
    project_code       VARCHAR(32)  NOT NULL,
    dvp_name           VARCHAR(128) NOT NULL,
    plan_start_time    DATE         NULL,
    plan_end_time      DATE         NULL,
    test_budget        DOUBLE       NULL,
    sample_car_count   INT          NULL,
    resource_allocation VARCHAR(256) NULL,
    status             VARCHAR(8)   NOT NULL,
    create_time        DATETIME     NOT NULL,
    update_time        DATETIME     NOT NULL,
    create_by          VARCHAR(64)  NOT NULL,
    PRIMARY KEY (dvp_code),
    KEY idx_dd_project (project_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_experiment_item_round (
    round_id          VARCHAR(32)  NOT NULL,
    dvp_code          VARCHAR(32)  NOT NULL,
    condition_code    VARCHAR(32)  NOT NULL,
    round_name        VARCHAR(128) NOT NULL,
    dev_phase         VARCHAR(16)  NOT NULL,
    milestone         VARCHAR(64)  NULL,
    round_start_time  DATE         NULL,
    round_end_time    DATE         NULL,
    status            VARCHAR(8)   NOT NULL,
    create_time       DATETIME     NOT NULL,
    update_time       DATETIME     NOT NULL,
    PRIMARY KEY (round_id),
    KEY idx_eir_dvp (dvp_code),
    KEY idx_eir_cond (condition_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS = 1;
