import "@/utils/sso";
import Cookies from "js-cookie";
import { getConfig } from "@/config";
import NProgress from "@/utils/progress";
import { transformI18n } from "@/plugins/i18n";
import { buildHierarchyTree } from "@/utils/tree";
import remainingRouter from "./modules/remaining";
import { useMultiTagsStoreHook } from "@/store/modules/multiTags";
import { usePermissionStoreHook } from "@/store/modules/permission";
import {
  isUrl,
  openLink,
  cloneDeep,
  isAllEmpty,
  storageLocal
} from "@pureadmin/utils";
import {
  ascending,
  getTopMenu,
  initRouter,
  isOneOfArray,
  getHistoryMode,
  findRouteByPath,
  handleAliveRoute,
  formatTwoStageRoutes,
  formatFlatteningRoutes
} from "./utils";
import {
  type Router,
  type RouteRecordRaw,
  type RouteComponent,
  createRouter
} from "vue-router";
import {
  type DataInfo,
  userKey,
  removeToken,
  multipleTabsKey,
  getToken
} from "@/utils/auth";
import { useUserStoreHook } from "@/store/modules/user";

/** access_token 过期时尝试用 refresh_token 续签。
 *  - 单飞守卫：并发路由切换只发一次 refresh 请求，其余 await 同一 promise
 *  - 成功 → true（cookie/localStorage 已被 setToken 更新）
 *  - 失败 → false（调用方应 logOut 跳登录页）
 *  解决问题：原 beforeEach 看到 tokenExpired 直接 removeToken + 跳 /login，
 *  axios 拦截器的 refresh 逻辑只在发请求时触发，路由切换先一步把用户踢了。 */
let refreshPromise: Promise<boolean> | null = null;
async function ensureFreshToken(refreshToken: string): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      await useUserStoreHook().handRefreshToken({ refreshToken });
      return true;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

/** 自动导入全部静态路由，无需再手动引入！匹配 src/router/modules 目录（任何嵌套级别）中具有 .ts 扩展名的所有文件，除了 remaining.ts 文件
 * 如何匹配所有文件请看：https://github.com/mrmlnc/fast-glob#basic-syntax
 * 如何排除文件请看：https://cn.vitejs.dev/guide/features.html#negative-patterns
 */
const modules: Record<string, any> = import.meta.glob(
  ["./modules/**/*.ts", "!./modules/**/remaining.ts"],
  {
    eager: true
  }
);

/** 原始静态路由（未做任何处理） */
const routes = [];

Object.keys(modules).forEach(key => {
  routes.push(modules[key].default);
});

/** 导出处理后的静态路由（三级及以上的路由全部拍成二级） */
export const constantRoutes: Array<RouteRecordRaw> = formatTwoStageRoutes(
  formatFlatteningRoutes(buildHierarchyTree(ascending(routes.flat(Infinity))))
);

/** 初始的静态路由，用于退出登录时重置路由 */
const initConstantRoutes: Array<RouteRecordRaw> = cloneDeep(constantRoutes);

/** 用于渲染菜单，保持原始层级 */
export const constantMenus: Array<RouteComponent> = ascending(
  routes.flat(Infinity)
).concat(...remainingRouter);

/** 不参与菜单的路由 */
export const remainingPaths = Object.keys(remainingRouter).map(v => {
  return remainingRouter[v].path;
});

/** 创建路由实例 */
export const router: Router = createRouter({
  history: getHistoryMode(import.meta.env.VITE_ROUTER_HISTORY),
  routes: constantRoutes.concat(...(remainingRouter as any)),
  strict: true,
  scrollBehavior(to, from, savedPosition) {
    return new Promise(resolve => {
      if (savedPosition) {
        return savedPosition;
      } else {
        if (from.meta.saveSrollTop) {
          const top: number =
            document.documentElement.scrollTop || document.body.scrollTop;
          resolve({ left: 0, top });
        }
      }
    });
  }
});

/** 记录已经加载的页面路径 */
const loadedPaths = new Set<string>();

/** 重置已加载页面记录 */
export function resetLoadedPaths() {
  loadedPaths.clear();
}

/** 重置路由 */
export function resetRouter() {
  router.clearRoutes();
  for (const route of initConstantRoutes.concat(...(remainingRouter as any))) {
    router.addRoute(route);
  }
  router.options.routes = formatTwoStageRoutes(
    formatFlatteningRoutes(buildHierarchyTree(ascending(routes.flat(Infinity))))
  );
  usePermissionStoreHook().clearAllCachePage();
  resetLoadedPaths();
}

/** 路由白名单 */
const whiteList = ["/login"];

const { VITE_HIDE_HOME } = import.meta.env;

router.beforeEach(async (to: ToRouteType, _from) => {
  to.meta.loaded = loadedPaths.has(to.path);

  if (!to.meta.loaded) {
    NProgress.start();
  }

  if (to.meta?.keepAlive) {
    handleAliveRoute(to, "add");
    // 页面整体刷新和点击标签页刷新
    if (_from.name === undefined || _from.name === "Redirect") {
      handleAliveRoute(to);
    }
  }
  const userInfo = storageLocal().getItem<DataInfo<number>>(userKey);

  // 检查 token 是否已过期（本地 expires 时间戳）
  const tokenData = getToken();
  let tokenExpired =
    !!tokenData && parseInt(String(tokenData.expires)) - Date.now() <= 0;

  // access_token 过期时先尝试 refresh，失败才踢人（避免 30 分钟一到就跳登录页）
  if (Cookies.get(multipleTabsKey) && userInfo && tokenExpired) {
    const ok = tokenData?.refreshToken
      ? await ensureFreshToken(tokenData.refreshToken)
      : false;
    if (ok) {
      tokenExpired = false; // refresh 成功，视为未过期，继续走正常路由
    } else {
      useUserStoreHook().logOut();
      return { path: "/login" };
    }
  }

  const externalLink = isUrl(to?.name as string);
  if (!externalLink) {
    to.matched.some(item => {
      if (!item.meta.title) return "";
      const Title = getConfig().Title;
      if (Title)
        document.title = `${transformI18n(item.meta.title)} | ${Title}`;
      else document.title = transformI18n(item.meta.title);
    });
  }
  /** 如果已经登录并存在登录信息后不能跳转到路由白名单，而是继续保持在当前页面 */
  function toCorrectRoute() {
    return whiteList.includes(to.fullPath) ? _from.fullPath : undefined;
  }
  if (Cookies.get(multipleTabsKey) && userInfo && !tokenExpired) {
    // 刷新场景（_from.name 为空）：wholeMenus 为空是 store 初始状态，不一定是无菜单权限。
    // 先 await initRouter() 加载菜单 + 恢复标签页，避免误判有菜单权限的账号（如 sysadmin）
    // 跳 /account-settings。原逻辑在下方 else 分支用 initRouter().then 异步，路由守卫同步返回，
    // 会先命中下方 wholeMenus === 0 判断导致误跳。
    if (
      !_from?.name &&
      usePermissionStoreHook().wholeMenus.length === 0 &&
      to.path !== "/login"
    ) {
      await initRouter();
      // 标签页恢复（未开启 multiTagsCache 时根据当前路由 push 一个标签）
      if (!useMultiTagsStoreHook().getMultiTagsCache) {
        const { path } = to;
        const route = findRouteByPath(
          path,
          router.options.routes[0].children
        );
        getTopMenu(true);
        if (route && route.meta?.title) {
          if (isAllEmpty(route.parentId) && route.meta?.backstage) {
            const { path, name, meta } = route.children[0];
            useMultiTagsStoreHook().handleTags("push", {
              path,
              name,
              meta
            });
          } else {
            const { path, name, meta } = route;
            useMultiTagsStoreHook().handleTags("push", {
              path,
              name,
              meta
            });
          }
        }
      }
      // 动态路由加载后 to.name 为空（目标路由是动态路由）时重新触发路由守卫，让动态路由生效
      if (isAllEmpty(to.name)) return to.fullPath;
    }
    // 已登录但 wholeMenus 为空（账号无管理菜单权限，如无角色用户登录 admin 后台，
    // 或只有 enduser-portal 角色的用户进 admin 后台）。
    // 不 removeToken——保留登录态让用户能进 /account-settings 做邮箱认证、改密码等
    // 个人信息维护。访问其他需要菜单权限的路由（如 /welcome、/system/*）时跳 /account-settings。
    // /login 放行是为了让用户主动退出登录回到登录页。
    if (
      usePermissionStoreHook().wholeMenus.length === 0 &&
      to.path !== "/login" &&
      to.path !== "/account-settings"
    ) {
      return { path: "/account-settings" };
    }
    // 无权限跳转403页面
    if (to.meta?.roles && !isOneOfArray(to.meta?.roles, userInfo?.roles)) {
      return { path: "/error/403" };
    }
    // 开启隐藏首页后在浏览器地址栏手动输入首页welcome路由则跳转到404页面
    if (VITE_HIDE_HOME === "true" && to.fullPath === "/welcome") {
      return { path: "/error/404" };
    }
    if (_from?.name) {
      // name为超链接
      if (externalLink) {
        openLink(to?.name as string);
        NProgress.done();
        return false;
      } else {
        return toCorrectRoute();
      }
    } else {
      // 刷新
      if (
        usePermissionStoreHook().wholeMenus.length === 0 &&
        to.path !== "/login"
      ) {
        initRouter().then((router: Router) => {
          if (!useMultiTagsStoreHook().getMultiTagsCache) {
            const { path } = to;
            const route = findRouteByPath(
              path,
              router.options.routes[0].children
            );
            getTopMenu(true);
            // query、params模式路由传参数的标签页不在此处处理
            if (route && route.meta?.title) {
              if (isAllEmpty(route.parentId) && route.meta?.backstage) {
                // 此处为动态顶级路由（目录）
                const { path, name, meta } = route.children[0];
                useMultiTagsStoreHook().handleTags("push", {
                  path,
                  name,
                  meta
                });
              } else {
                const { path, name, meta } = route;
                useMultiTagsStoreHook().handleTags("push", {
                  path,
                  name,
                  meta
                });
              }
            }
          }
          // 确保动态路由完全加入路由列表并且不影响静态路由（注意：动态路由刷新时router.beforeEach可能会触发两次，第一次触发动态路由还未完全添加，第二次动态路由才完全添加到路由列表，如果需要在router.beforeEach做一些判断可以在to.name存在的条件下去判断，这样就只会触发一次）
          if (isAllEmpty(to.name)) router.push(to.fullPath);
        });
      }
      return toCorrectRoute();
    }
  } else {
    if (to.path !== "/login") {
      if (whiteList.indexOf(to.path) !== -1 || to.meta?.noAuth) {
        return true;
      } else {
        removeToken();
        return { path: "/login" };
      }
    } else {
      return true;
    }
  }
});

router.afterEach(to => {
  loadedPaths.add(to.path);
  NProgress.done();
});

export default router;
