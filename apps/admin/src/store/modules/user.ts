import Cookies from "js-cookie";
import { defineStore } from "pinia";
import {
  type userType,
  store,
  router,
  resetRouter,
  routerArrays,
  storageLocal
} from "../utils";
import {
  getLogin,
  refreshTokenApi,
  getMine,
  logoutApi,
  loginByContact,
  loginBySmsCode
} from "@/api/user";
import { useMultiTagsStoreHook } from "./multiTags";
import {
  type DataInfo,
  setToken,
  removeToken,
  getToken,
  userKey,
  TokenKey,
  multipleTabsKey
} from "@/utils/auth";

/** 通用登录响应处理 — 先写 cookie 让 /me 能带 token，拉 /me 拿真实 roles 后再 setToken。
 * 抽出来给 loginByUsername / loginByContact / loginBySmsCode 复用。
 * usernameFallback 在 /me 返回 username 缺失时兜底。
 *
 * 时序说明（修历史 bug）：原实现先 setToken(roles=[]) 再 await getMine，getMine 失败时
 * catch 只 set this.username 不补 localStorage → cookie 有 token 但 localStorage.roles
 * 残留为 [] → 刷新时 filterNoPermissionTree 把 wholeMenus 过滤空 → 误跳 /account-settings。
 * 现改为：cookie 先行 → getMine 失败则 removeToken + 抛错，不进入半登录态。 */
async function processLoginResponse(
  this: any,
  res: any,
  usernameFallback: string
) {
  const expires = Date.now() + 30 * 60 * 1000;

  const cookieString = JSON.stringify({
    accessToken: res.access_token,
    expires,
    refreshToken: res.refresh_token
  });
  Cookies.set(TokenKey, cookieString, {
    expires: (expires - Date.now()) / 86400000
  });
  Cookies.set(multipleTabsKey, "true");

  let userInfo: any;
  try {
    userInfo = await getMine();
  } catch (err: any) {
    removeToken();
    err.__loginStage = "fetch_user_info";
    throw err;
  }

  setToken({
    accessToken: res.access_token,
    refreshToken: res.refresh_token,
    expires,
    username: userInfo.username || usernameFallback,
    roles: userInfo.roles || [],
    permissions: [],
    avatar: userInfo.avatar_url || "",
    nickname: userInfo.real_name || userInfo.nickname || ""
  } as any);

  this.username = userInfo.username || usernameFallback;
  this.roles = userInfo.roles || [];
  this.avatar = userInfo.avatar_url || "";
  this.nickname = userInfo.real_name || userInfo.nickname || "";
  this.permissions = [];

  const stored: DataInfo<number> =
    storageLocal().getItem<DataInfo<number>>(userKey) ||
    ({} as DataInfo<number>);
  stored.isPlatformAdmin = !!userInfo.is_platform_admin;
  stored.emailVerified = !!userInfo.email_verified;
  stored.phoneVerified = !!userInfo.phone_verified;
  storageLocal().setItem(userKey, stored);

  return res;
}

export const useUserStore = defineStore("pure-user", {
  state: (): userType => ({
    avatar: storageLocal().getItem<DataInfo<number>>(userKey)?.avatar ?? "",
    username: storageLocal().getItem<DataInfo<number>>(userKey)?.username ?? "",
    nickname: storageLocal().getItem<DataInfo<number>>(userKey)?.nickname ?? "",
    roles: storageLocal().getItem<DataInfo<number>>(userKey)?.roles ?? [],
    permissions:
      storageLocal().getItem<DataInfo<number>>(userKey)?.permissions ?? [],
    verifyCode: "",
    currentPage: 0,
    isLoggingOut: false
  }),
  actions: {
    SET_AVATAR(avatar: string) { this.avatar = avatar; },
    SET_USERNAME(username: string) { this.username = username; },
    SET_NICKNAME(nickname: string) { this.nickname = nickname; },
    SET_ROLES(roles: Array<string>) { this.roles = roles; },
    SET_PERMS(permissions: Array<string>) { this.permissions = permissions; },
    SET_VERIFYCODE(verifyCode: string) { this.verifyCode = verifyCode; },
    SET_CURRENTPAGE(value: number) { this.currentPage = value; },

    async loginByUsername(data) {
      const res: any = await getLogin(data);
      return processLoginResponse.call(this, res, data.username);
    },

    /** 邮箱/手机 + 密码登录（contact 必须已认证）。
     *  fallback username 用 contact（邮箱或手机号），/me 拉回真实 username 后覆盖。 */
    async loginByContact(data: {
      contact_type: "email" | "phone";
      contact: string;
      password: string;
      captcha_id?: string;
      captcha_answer?: string;
    }) {
      const res: any = await loginByContact(data);
      return processLoginResponse.call(this, res, data.contact);
    },

    /** 手机号 + 验证码登录（无需密码，code 即所有权证明）。
     *  fallback username 用 phone，/me 拉回真实 username 后覆盖。 */
    async loginBySmsCode(data: { phone: string; code: string }) {
      const res: any = await loginBySmsCode(data);
      return processLoginResponse.call(this, res, data.phone);
    },

    async logOut() {
      // 重入守卫：logOut 可能在多处被并发触发（router beforeEach / 请求拦截器 refresh
      // 失败 catch / 响应拦截器 401 handler / navbar 退出按钮），若不挡住，第一个 logOut
      // 还在 await logoutApi 时，第二个 logOut 又起一个 logoutApi，token 过期 → 请求拦截器
      // 触发 refresh → refresh 失败 → 再调 logOut → "登录已过期" toast 死循环。
      if (this.isLoggingOut) return;
      this.isLoggingOut = true;
      try {
        // 调后端记录 auth.logout 审计日志（best-effort，失败不阻塞本地登出）。
        // token 已过期时跳过——否则 logoutApi 会触发 refresh cycle（/auth/logout 不在
        // 请求白名单里），又把死循环引回来。
        const tk = getToken();
        const expired = tk && parseInt(String(tk.expires)) - Date.now() <= 0;
        if (!expired) {
          try {
            await logoutApi();
          } catch {
            // token 失效/网络问题都不影响本地登出
          }
        }
        this.username = "";
        this.roles = [];
        this.permissions = [];
        removeToken();
        useMultiTagsStoreHook().handleTags("equal", [...routerArrays]);
        resetRouter();
        router.push("/login");
      } finally {
        this.isLoggingOut = false;
      }
    },

    async handRefreshToken(data) {
      const res: any = await refreshTokenApi(data);
      const expires = Date.now() + 30 * 60 * 1000;
      const tokenData: any = {
        accessToken: res.access_token,
        refreshToken: res.refresh_token,
        expires,
        username: this.username,
        roles: this.roles,
        permissions: this.permissions,
      };
      setToken(tokenData);
      return { data: { accessToken: res.access_token } };
    }
  }
});

export function useUserStoreHook() {
  return useUserStore(store);
}
