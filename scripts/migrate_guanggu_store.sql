-- ============================================================
-- 光谷门店企业微信 E2E 测试 — 数据迁移脚本
-- 1. 清空现有业务数据（保留 admin/roles/permissions/engine_instances）
-- 2. 插入测试场景数据
-- ============================================================

BEGIN;

-- ============================================================
-- Phase 1: 清空业务数据（按 FK 依赖顺序）
-- ============================================================

DELETE FROM agent_sessions;
DELETE FROM agent_profiles;
DELETE FROM agent_group_access;
DELETE FROM agent_user_access;
DELETE FROM agent_channels;
DELETE FROM agent_deployments;
DELETE FROM agents;
DELETE FROM user_group_members;
DELETE FROM user_roles WHERE user_id != (SELECT id FROM users WHERE username = 'admin');
DELETE FROM users WHERE username != 'admin';
DELETE FROM user_groups;

-- ============================================================
-- Phase 2: 插入光谷门店测试数据
-- ============================================================

-- 2.1 测试用户 (密码: test123)
INSERT INTO users (id, username, email, hashed_password, is_active, created_at, updated_at) VALUES
(
  '40767032-abbe-4848-a328-dda17c4a37a5',
  'store_mgr',
  'store_mgr@test.unionagents.cn',
  '$2b$12$Pd5Fr6C7pPKm858W5aki3eYh3uUaOesJCz9oW7MgDv84qR.RPvLMi',
  true, NOW(), NOW()
),
(
  '83c83c77-04a2-4b96-b77b-ada92922e0c1',
  'staff_a',
  'staff_a@test.unionagents.cn',
  '$2b$12$Pd5Fr6C7pPKm858W5aki3eYh3uUaOesJCz9oW7MgDv84qR.RPvLMi',
  true, NOW(), NOW()
),
(
  'c79cfbd0-eb59-4643-ae43-4833440cccbd',
  'staff_b',
  'staff_b@test.unionagents.cn',
  '$2b$12$Pd5Fr6C7pPKm858W5aki3eYh3uUaOesJCz9oW7MgDv84qR.RPvLMi',
  true, NOW(), NOW()
),
(
  'bdeec322-b540-4490-b974-db5b7171d9be',
  'customer',
  'customer@test.unionagents.cn',
  '$2b$12$Pd5Fr6C7pPKm858W5aki3eYh3uUaOesJCz9oW7MgDv84qR.RPvLMi',
  true, NOW(), NOW()
);

-- 分配「终端用户」角色给测试用户
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM users u, roles r
WHERE u.username IN ('store_mgr', 'staff_a', 'staff_b', 'customer')
  AND r.name = '终端用户';

-- 2.2 用户组：门店员工组
INSERT INTO user_groups (id, name, created_at, updated_at) VALUES
(
  '1c09f249-9b72-4a1e-8260-0b95ee422fb4',
  '门店员工组',
  NOW(), NOW()
);

-- 店长、店员A、店员B 加入门店员工组
INSERT INTO user_group_members (user_id, group_id) VALUES
  ('40767032-abbe-4848-a328-dda17c4a37a5', '1c09f249-9b72-4a1e-8260-0b95ee422fb4'),
  ('83c83c77-04a2-4b96-b77b-ada92922e0c1', '1c09f249-9b72-4a1e-8260-0b95ee422fb4'),
  ('c79cfbd0-eb59-4643-ae43-4833440cccbd', '1c09f249-9b72-4a1e-8260-0b95ee422fb4');

-- 2.3 智能体（使用光谷门店资源池引擎实例）
-- 引擎实例 ID: a851c94c-b234-4024-984f-4fc3a4ca721b (光谷门店资源池)
-- 创建者: admin
INSERT INTO agents (id, name, description, engine_type, status, access_scope, created_by,
                    engine_instance_id, created_at, updated_at, published_at) VALUES
(
  'd38e436e-a4ae-4706-8b24-93e3f9d7bd15',
  '门店助手',
  '光谷门店智能助手，面向所有用户提供门店咨询服务',
  'HERMES', 'PUBLISHED', 'ALL',
  (SELECT id FROM users WHERE username = 'admin'),
  'a851c94c-b234-4024-984f-4fc3a4ca721b',
  NOW(), NOW(), NOW()
),
(
  'c1ef2f5e-6457-401d-b9a9-f0466b4f1005',
  '库存助手',
  '光谷门店库存管理助手，仅门店员工可用',
  'HERMES', 'PUBLISHED', 'USER_GROUP',
  (SELECT id FROM users WHERE username = 'admin'),
  'a851c94c-b234-4024-984f-4fc3a4ca721b',
  NOW(), NOW(), NOW()
),
(
  '07a72439-0417-4455-a11e-ba4035ec8d4c',
  '店长助理',
  '光谷门店店长专属助理，仅店长可用',
  'HERMES', 'PUBLISHED', 'USER',
  (SELECT id FROM users WHERE username = 'admin'),
  'a851c94c-b234-4024-984f-4fc3a4ca721b',
  NOW(), NOW(), NOW()
);

-- 2.4 企业微信通道配置
-- 配置说明:
--   token / encoding_aes_key: 需与企业微信后台「接收消息」配置一致
--   corp_id / secret / agent_id: 来自企业微信应用管理后台
--
-- 门店助手: ALL scope → INDEPENDENT profile（每个用户独立会话）
INSERT INTO agent_channels (id, agent_id, channel_type, config, enabled, scope_type, profile_type,
                            callback_url, created_at, updated_at) VALUES
(
  'a23b7a17-fcf4-503c-b021-7e895ecbf433',
  'd38e436e-a4ae-4706-8b24-93e3f9d7bd15',
  'wecom',
  '{
    "token": "guanggu_token_2024",
    "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
    "corp_id": "ww_changeme",
    "secret": "changeme_secret",
    "agent_id": "1000001"
  }'::json,
  true, 'ALL', 'INDEPENDENT',
  '/api/gateway/channel/wecom/d38e436e-a4ae-4706-8b24-93e3f9d7bd15/callback',
  NOW(), NOW()
),
-- 库存助手: USER_GROUP scope → SHARED profile（同组共享会话）
(
  '4b7023e4-e714-5a4b-a761-b290970754dd',
  'c1ef2f5e-6457-401d-b9a9-f0466b4f1005',
  'wecom',
  '{
    "token": "guanggu_token_2024",
    "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
    "corp_id": "ww_changeme",
    "secret": "changeme_secret",
    "agent_id": "1000002"
  }'::json,
  true, 'USER_GROUP', 'SHARED',
  '/api/gateway/channel/wecom/c1ef2f5e-6457-401d-b9a9-f0466b4f1005/callback',
  NOW(), NOW()
),
-- 店长助理: USER scope → INDEPENDENT profile
(
  'fa86b4cc-f33e-52fa-9554-90547146bf00',
  '07a72439-0417-4455-a11e-ba4035ec8d4c',
  'wecom',
  '{
    "token": "guanggu_token_2024",
    "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
    "corp_id": "ww_changeme",
    "secret": "changeme_secret",
    "agent_id": "1000003"
  }'::json,
  true, 'USER', 'INDEPENDENT',
  '/api/gateway/channel/wecom/07a72439-0417-4455-a11e-ba4035ec8d4c/callback',
  NOW(), NOW()
);

-- 2.5 Channel scope_target_id 修正
-- 库存助手 → USER_GROUP scope, target = 门店员工组
UPDATE agent_channels
SET scope_target_id = '1c09f249-9b72-4a1e-8260-0b95ee422fb4'
WHERE agent_id = 'c1ef2f5e-6457-401d-b9a9-f0466b4f1005';

-- 店长助理 → USER scope, target = 店长
UPDATE agent_channels
SET scope_target_id = '40767032-abbe-4848-a328-dda17c4a37a5'
WHERE agent_id = '07a72439-0417-4455-a11e-ba4035ec8d4c';

-- 2.6 访问控制：店长 → 店长助理 (USER scope)
INSERT INTO agent_user_access (agent_id, user_id) VALUES
(
  '07a72439-0417-4455-a11e-ba4035ec8d4c',
  '40767032-abbe-4848-a328-dda17c4a37a5'
);

-- 2.7 访问控制：库存助手 → 门店员工组 (USER_GROUP scope)
INSERT INTO agent_group_access (agent_id, group_id) VALUES
(
  'c1ef2f5e-6457-401d-b9a9-f0466b4f1005',
  '1c09f249-9b72-4a1e-8260-0b95ee422fb4'
);

COMMIT;

-- ============================================================
-- 验证：显示迁移结果
-- ============================================================
\echo '=== 用户 ==='
SELECT id, username, email, is_active FROM users ORDER BY username;

\echo '=== 用户组 ==='
SELECT ug.id, ug.name, COUNT(ugm.user_id) AS member_count
FROM user_groups ug
LEFT JOIN user_group_members ugm ON ug.id = ugm.group_id
GROUP BY ug.id;

\echo '=== 智能体 ==='
SELECT id, name, access_scope, status FROM agents ORDER BY name;

\echo '=== 通道 ==='
SELECT ac.agent_id, a.name AS agent_name, ac.channel_type, ac.scope_type, ac.profile_type, ac.enabled
FROM agent_channels ac
JOIN agents a ON ac.agent_id = a.id;

\echo '=== 访问控制 ==='
SELECT 'USER_ACCESS' AS type, a.name AS agent_name, u.username
FROM agent_user_access aua
JOIN agents a ON aua.agent_id = a.id
JOIN users u ON aua.user_id = u.id
UNION ALL
SELECT 'GROUP_ACCESS', a.name, ug.name
FROM agent_group_access aga
JOIN agents a ON aga.agent_id = a.id
JOIN user_groups ug ON aga.group_id = ug.id;
