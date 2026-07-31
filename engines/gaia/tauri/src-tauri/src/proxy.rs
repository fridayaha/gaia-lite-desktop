//! axum 反向代理：把 webview 同源请求转发到 Python sidecar 动态端口。
//!
//! 规则照搬 src/web-ui/vite.config.ts:23-44 的 proxy 表：
//!   /ontologies /objects /ai /api /health /metrics → sidecar
//!   /actions：Accept: text/html 走 SPA index.html，否则 → sidecar（bypass）
//!   /api/auth：lite 关 auth（前端 VITE_AUTH_ENABLED=false），返回 404
//!   静态资源（/、/assets/*、非代理前缀）→ tauri/dist，未命中文件回 index.html
//!
//! 路由优先级（axum 按注册顺序匹配，先注册先匹配）：
//!   1. /api/auth → 404（lite 关 auth）
//!   2. /actions → bypass（text/html 走 SPA，否则代理）
//!   3. 代理前缀 catch-all（/ontologies /objects /ai /api /health /metrics 及子路径）→ sidecar
//!   4. 静态资源 + SPA fallback → ServeDir（fallback index.html）
//!
//! SSE 禁缓冲：/ai/agent 响应用 reqwest bytes_stream() + Body::from_stream，
//! 不 .bytes()，保留 text/event-stream + X-Accel-Buffering 头透传。
//! 前端用 fetch+ReadableStream（不依赖 EventSource），同源后无 CORS。

use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, HeaderValue, Method, StatusCode},
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use std::path::PathBuf;

/// 转发到 sidecar 的路径前缀（匹配即代理，不走静态资源）。
const PROXY_PREFIXES: &[&str] = &[
    "/ontologies",
    "/objects",
    "/ai",
    "/api",
    "/health",
    "/metrics",
];

#[derive(Clone)]
struct ProxyState {
    backend_url: String,
    client: reqwest::Client,
    dist_dir: PathBuf,
}

/// 构建 axum 反代 Router。
///
/// - `backend_url`：sidecar 地址，如 `http://127.0.0.1:54321`
/// - `dist_dir`：前端静态资源目录（tauri/dist）
pub fn proxy_router(backend_url: String, dist_dir: PathBuf) -> Router {
    let client = reqwest::Client::builder()
        .build()
        .expect("reqwest client build");
    let state = ProxyState {
        backend_url,
        client,
        dist_dir: dist_dir.clone(),
    };

    // catch-all 路由：所有请求进 dispatch，内部按前缀分流。
    // axum 的 ServeDir 作为 dispatch 内部的静态资源分支处理。
    Router::new()
        .route("/*path", any(dispatch))
        .route("/", any(dispatch))
        .with_state(state)
}

/// 路径分流动作（纯逻辑，便于单测覆盖分流规则）。
#[derive(Debug, PartialEq, Eq)]
enum PathAction {
    /// /api/auth — lite 关 auth，返回 404
    AuthDisabled,
    /// /actions — 页面导航走 SPA，API 请求代理（bypass）
    ActionsBypass,
    /// 代理前缀（/ontologies /objects /ai /api /health /metrics）→ sidecar
    Proxy,
    /// 静态资源 + SPA fallback
    Static,
}

/// 按路径前缀分类（纯函数）。dispatch 调它决定分支。
fn classify_path(path: &str) -> PathAction {
    if path == "/api/auth" || path.starts_with("/api/auth/") {
        return PathAction::AuthDisabled;
    }
    if path == "/actions" || path.starts_with("/actions/") {
        return PathAction::ActionsBypass;
    }
    if PROXY_PREFIXES.iter().any(|p| path.starts_with(p)) {
        return PathAction::Proxy;
    }
    PathAction::Static
}

/// 统一分发：按路径前缀决定代理 / SPA / 静态资源。
async fn dispatch(State(state): State<ProxyState>, req: Request) -> Response {
    let path = req.uri().path().to_string();
    match classify_path(&path) {
        PathAction::AuthDisabled => (
            StatusCode::NOT_FOUND,
            "auth disabled in lite edition",
        )
            .into_response(),
        PathAction::ActionsBypass => {
            if is_html_navigation(&req) {
                serve_index(&state.dist_dir).await
            } else {
                forward(req, state).await
            }
        }
        PathAction::Proxy => forward(req, state).await,
        PathAction::Static => serve_static_or_index(req, state).await,
    }
}

/// 静态资源：先尝试 dist 下文件，未命中回 index.html（SPA 客户端路由）。
async fn serve_static_or_index(_req: Request, state: ProxyState) -> Response {
    let path = _req.uri().path().trim_start_matches('/');
    let file_path = if path.is_empty() {
        state.dist_dir.join("index.html")
    } else {
        state.dist_dir.join(path)
    };

    // 安全：禁止路径穿越（../）
    if !file_path.starts_with(&state.dist_dir) {
        return (StatusCode::FORBIDDEN, "path traversal").into_response();
    }

    if file_path.is_file() {
        match tokio::fs::read(&file_path).await {
            Ok(bytes) => {
                let ct = guess_content_type(&file_path);
                Response::builder()
                    .status(StatusCode::OK)
                    .header(header::CONTENT_TYPE, ct)
                    .body(Body::from(bytes))
                    .unwrap()
            }
            Err(_) => serve_index(&state.dist_dir).await,
        }
    } else {
        // SPA fallback：未命中的路径交给 React 客户端路由
        serve_index(&state.dist_dir).await
    }
}

async fn serve_index(dist_dir: &PathBuf) -> Response {
    let index = dist_dir.join("index.html");
    match tokio::fs::read(&index).await {
        Ok(bytes) => Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
            .body(Body::from(bytes))
            .unwrap(),
        Err(_) => (StatusCode::NOT_FOUND, "index.html missing").into_response(),
    }
}

fn is_html_navigation(req: &Request) -> bool {
    req.headers()
        .get(header::ACCEPT)
        .and_then(|v| v.to_str().ok())
        .map(|a| a.contains("text/html"))
        .unwrap_or(false)
}

/// 核心转发：流式复制请求/响应（SSE 友好）。
async fn forward(req: Request, state: ProxyState) -> Response {
    let (parts, body) = req.into_parts();
    let path_and_query = parts
        .uri
        .path_and_query()
        .map(|p| p.as_str().to_string())
        .unwrap_or_else(|| parts.uri.path().to_string());
    let upstream = format!("{}{}", state.backend_url, path_and_query);

    let method = Method::from_bytes(parts.method.as_str().as_bytes()).unwrap_or(Method::GET);
    let mut req_builder = state.client.request(method, &upstream);
    for (name, value) in parts.headers.iter() {
        if is_hop_by_hop(name.as_str()) || name == "host" || name == "origin" || name == "referer" {
            continue;
        }
        req_builder = req_builder.header(name, value);
    }
    let req_body = reqwest::Body::wrap_stream(body.into_data_stream());
    req_builder = req_builder.body(req_body);

    let upstream_resp = match req_builder.send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("proxy forward to {} failed: {}", upstream, e);
            return (
                StatusCode::BAD_GATEWAY,
                format!("backend unreachable: {e}"),
            )
                .into_response();
        }
    };

    let status = upstream_resp.status();
    let mut resp_builder = Response::builder().status(status);
    for (name, value) in upstream_resp.headers().iter() {
        if is_hop_by_hop(name.as_str()) {
            continue;
        }
        resp_builder = resp_builder.header(name, value);
    }
    // 显式标记禁缓冲（防中间层缓冲 SSE）。
    resp_builder = resp_builder.header("x-accel-buffering", HeaderValue::from_static("no"));
    let stream = upstream_resp.bytes_stream();
    resp_builder
        .body(Body::from_stream(stream))
        .unwrap_or_else(|_| (StatusCode::BAD_GATEWAY, "stream build failed").into_response())
}

fn is_hop_by_hop(name: &str) -> bool {
    matches!(
        name.to_lowercase().as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "te"
            | "trailers"
            | "transfer-encoding"
            | "upgrade"
    )
}

fn guess_content_type(path: &PathBuf) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_lowercase())
        .as_deref()
    {
        Some("html") => "text/html; charset=utf-8",
        Some("js") | Some("mjs") => "application/javascript; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("json") => "application/json; charset=utf-8",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        Some("woff") => "font/woff",
        Some("woff2") => "font/woff2",
        _ => "application/octet-stream",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_classify_path_proxy_prefixes() {
        // 代理前缀 → sidecar（照搬 vite.config.ts proxy 表）
        assert_eq!(classify_path("/ontologies"), PathAction::Proxy);
        assert_eq!(classify_path("/ontologies/Hr/object-types"), PathAction::Proxy);
        assert_eq!(classify_path("/objects/Some/df"), PathAction::Proxy);
        assert_eq!(classify_path("/ai/agent"), PathAction::Proxy);
        assert_eq!(classify_path("/api/datasources"), PathAction::Proxy);
        assert_eq!(classify_path("/health"), PathAction::Proxy);
        assert_eq!(classify_path("/metrics"), PathAction::Proxy);
    }

    #[test]
    fn test_classify_path_actions_bypass() {
        // /actions 既是 API 前缀又是页面路由 → bypass（text/html 走 SPA，否则代理）
        assert_eq!(classify_path("/actions"), PathAction::ActionsBypass);
        assert_eq!(classify_path("/actions/execute"), PathAction::ActionsBypass);
        assert_eq!(classify_path("/actions/definitions"), PathAction::ActionsBypass);
    }

    #[test]
    fn test_classify_path_auth_disabled() {
        // lite 关 auth（VITE_AUTH_ENABLED=false）
        assert_eq!(classify_path("/api/auth"), PathAction::AuthDisabled);
        assert_eq!(classify_path("/api/auth/signin"), PathAction::AuthDisabled);
    }

    #[test]
    fn test_classify_path_static_and_spa() {
        // 静态资源 + SPA fallback
        assert_eq!(classify_path("/"), PathAction::Static);
        assert_eq!(classify_path("/assets/index-Abc.js"), PathAction::Static);
        assert_eq!(classify_path("/some/spa/route"), PathAction::Static);
    }

    #[test]
    fn test_is_hop_by_hop() {
        // hop-by-hop 头应被剥离（不转发）
        assert!(is_hop_by_hop("connection"));
        assert!(is_hop_by_hop("Connection"));
        assert!(is_hop_by_hop("transfer-encoding"));
        assert!(is_hop_by_hop("keep-alive"));
        assert!(is_hop_by_hop("upgrade"));
        // 大小写不敏感
        assert!(is_hop_by_hop("PROXY-AUTHENTICATE"));
        // 非 hop-by-hop 保留
        assert!(!is_hop_by_hop("content-type"));
        assert!(!is_hop_by_hop("x-trace-id"));
        assert!(!is_hop_by_hop("authorization"));
    }

    #[test]
    fn test_guess_content_type() {
        assert_eq!(guess_content_type(&PathBuf::from("index.html")), "text/html; charset=utf-8");
        assert_eq!(
            guess_content_type(&PathBuf::from("assets/app.js")),
            "application/javascript; charset=utf-8"
        );
        assert_eq!(guess_content_type(&PathBuf::from("style.css")), "text/css; charset=utf-8");
        assert_eq!(guess_content_type(&PathBuf::from("logo.svg")), "image/svg+xml");
        assert_eq!(guess_content_type(&PathBuf::from("data.json")), "application/json; charset=utf-8");
        assert_eq!(
            guess_content_type(&PathBuf::from("icon.png")),
            "image/png"
        );
        // 未知扩展
        assert_eq!(
            guess_content_type(&PathBuf::from("file.wasm")),
            "application/octet-stream"
        );
    }
}
