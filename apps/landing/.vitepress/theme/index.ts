import DefaultTheme from "vitepress/theme";
import { h } from "vue";
import LandingCTA from "./components/LandingCTA.vue";
import VersionFooter from "./components/VersionFooter.vue";
import DownloadCard from "./components/DownloadCard.vue";
import "./style.css";

export default {
  extends: DefaultTheme,
  Layout: () =>
    h(DefaultTheme.Layout, null, {
      "home-hero-after": () => h(LandingCTA),
    }),
  enhanceApp(ctx) {
    // 注册全局组件，markdown 里可直接 <VersionFooter /> / <DownloadCard /> 使用
    ctx.app.component("VersionFooter", VersionFooter);
    ctx.app.component("DownloadCard", DownloadCard);

    if (typeof window !== "undefined") {
      // nav 里指向 /docs/ 或 /download 的链接加 target="_top"，绕过 VitePress SPA router
      // landing 只有 / 和 /en/ 两个路由，/docs/* 走 client-side navigate 会 404；
      // /download 走 SPA router 在某些场景点击无反应，统一 full page load 更稳
      // VitePress router 检查 anchor 的 target，非 _self 时不拦截，浏览器 full page load
      const patchNavLinks = () => {
        document
          .querySelectorAll<HTMLAnchorElement>(
            ".VPNavBarMenuLink[href^='/docs/'], .VPNavBarMenuLink[href^='/download'], .VPNavBarMenuLink[href^='/en/download']",
          )
          .forEach((a) => {
            a.target = "_top";
          });
      };
      setTimeout(patchNavLinks, 200);
      ctx.router.onAfterRouteChange = () => {
        setTimeout(patchNavLinks, 100);
      };
    }
  },
};
