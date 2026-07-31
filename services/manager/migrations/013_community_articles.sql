-- 013: 社区文章表（全局平台级，无 group 隔离）
-- Base.metadata.create_all 在 manager 启动时自动建新表（仅对全新部署生效）；
-- 已存在的库需执行此 SQL 兜底（幂等，可重复执行）。

CREATE TABLE IF NOT EXISTS community_articles (
    id              UUID PRIMARY KEY,
    author_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(200) NOT NULL,
    slug            VARCHAR(200) NOT NULL,
    excerpt         VARCHAR(500),
    content         TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    reject_reason   TEXT,
    published_at    TIMESTAMPTZ,
    view_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_article_slug UNIQUE (slug)
);

-- 公开列表查询：WHERE status='PUBLISHED' ORDER BY published_at DESC
CREATE INDEX IF NOT EXISTS ix_articles_status_published_at
    ON community_articles(status, published_at);

-- 我的文章查询：WHERE author_id=? ORDER BY updated_at DESC
CREATE INDEX IF NOT EXISTS ix_articles_author_id
    ON community_articles(author_id);

-- slug 查询（UNIQUE 约束已自带索引，这里显式建以便查询计划明确）
CREATE INDEX IF NOT EXISTS ix_articles_slug
    ON community_articles(slug);
