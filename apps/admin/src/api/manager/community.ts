import { http } from "@/utils/http";

export type ArticleStatus = "DRAFT" | "PENDING" | "PUBLISHED" | "REJECTED";

export type ArticleListItem = {
  id: string;
  author_id: string;
  author_name: string | null;
  title: string;
  slug: string;
  excerpt: string | null;
  status: ArticleStatus;
  published_at: string | null;
  view_count: number;
  created_at: string;
};

export type ArticleResponse = ArticleListItem & {
  content: string;
  reject_reason: string | null;
  updated_at: string;
};

export type ArticleListResponse = {
  items: ArticleListItem[];
  total: number;
  page: number;
  page_size: number;
};

export type ArticleCreate = {
  title: string;
  slug?: string;
  excerpt?: string;
  content: string;
};

export type ArticleUpdate = {
  title?: string;
  excerpt?: string;
  content?: string;
};

export type ArticleAudit = {
  approve: boolean;
  reject_reason?: string;
};

// ── 公开端点（无需登录）──

export const getPublicArticlesApi = (params: {
  page?: number;
  page_size?: number;
  q?: string;
}) =>
  http.request<ArticleListResponse>("get", "/api/manager/community/articles", {
    params,
  });

export const getPublicArticleApi = (slugOrId: string) =>
  http.request<ArticleResponse>(
    "get",
    `/api/manager/community/articles/${slugOrId}`
  );

// ── 登录用户端点 ──

export const getMyArticlesApi = () =>
  http.request<ArticleListItem[]>("get", "/api/manager/community/my-articles");

export const createArticleApi = (data: ArticleCreate) =>
  http.request<ArticleResponse>("post", "/api/manager/community/articles", {
    data,
  });

export const updateArticleApi = (id: string, data: ArticleUpdate) =>
  http.request<ArticleResponse>("put", `/api/manager/community/articles/${id}`, {
    data,
  });

export const deleteArticleApi = (id: string) =>
  http.request<void>("delete", `/api/manager/community/articles/${id}`);

export const submitArticleApi = (id: string) =>
  http.request<ArticleResponse>(
    "post",
    `/api/manager/community/articles/${id}/submit`
  );

// ── 审核端点 ──

export const getPendingArticlesApi = (params: {
  page?: number;
  page_size?: number;
}) =>
  http.request<ArticleListResponse>(
    "get",
    "/api/manager/community/audit/pending",
    { params }
  );

export const auditArticleApi = (id: string, data: ArticleAudit) =>
  http.request<ArticleResponse>(
    "post",
    `/api/manager/community/articles/${id}/audit`,
    { data }
  );
