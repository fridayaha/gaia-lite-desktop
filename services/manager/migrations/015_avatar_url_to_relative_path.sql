-- 015_avatar_url_to_relative_path.sql
-- 把 users.avatar_url 从绝对 URL（http://minio.<host>/...）改成相对路径（/avatars/...）
-- 原因：nip.io 公共 DNS host 在公网访问被 reset，浏览器加载不到头像。
-- 改成 /avatars/<bucket>/<key> 相对路径，经 admin nginx 反代到 minio ClusterIP。
-- 同时兼容本地 k3s（nip.io）和云上 ECS（任意 host 前缀）。
-- nginx config 已加 location /avatars/ proxy_pass http://minio.unionagents.svc.cluster.local:9000/
-- (proxy_pass 末尾 / 让 nginx 去掉 /avatars/ 前缀，minio 收到的 path 为 /<bucket>/<key>)

-- 去掉 host 前缀（http(s)://<host> → 空），保留 path 部分，前面加 /avatars
-- 'http://minio.190.92.230.115.nip.io/unionagents-avatars/avatars/uid/key.png'
--   → '/avatars/unionagents-avatars/avatars/uid/key.png'
UPDATE users
SET avatar_url = '/avatars' || REGEXP_REPLACE(avatar_url, '^https?://[^/]+', '')
WHERE avatar_url ~ '^https?://';
