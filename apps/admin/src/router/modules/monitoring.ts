import { $t } from "@/plugins/i18n";
import { monitoring } from "@/router/enums";

const MONITORING_ROLES = ["系统管理员", "平台管理员", "运维人员"];

export default {
  path: "/monitoring",
  redirect: "/monitoring/trace",
  meta: {
    icon: "ri:pulse-line",
    title: $t("menus.pureMonitoring"),
    rank: monitoring,
    roles: MONITORING_ROLES
  },
  children: [
    {
      path: "/monitoring/trace",
      name: "MonitoringTrace",
      component: () => import("@/views/monitoring/trace/index.vue"),
      meta: { icon: "ri:route-line", title: $t("menus.pureMonitoringTrace"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/resources",
      name: "MonitoringResources",
      component: () => import("@/views/monitoring/resources/index.vue"),
      meta: { icon: "ri:server-line", title: $t("menus.pureMonitoringResources"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/service-health",
      name: "MonitoringServiceHealth",
      component: () => import("@/views/monitoring/service-health/index.vue"),
      meta: { icon: "ri:heart-pulse-line", title: $t("menus.pureMonitoringServiceHealth"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/usage",
      name: "MonitoringUsage",
      component: () => import("@/views/monitoring/usage/index.vue"),
      meta: { icon: "ri:coins-line", title: $t("menus.pureMonitoringUsage"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/calls",
      name: "MonitoringCalls",
      component: () => import("@/views/monitoring/calls/index.vue"),
      meta: { icon: "ri:bar-chart-2-line", title: $t("menus.pureMonitoringCalls"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/operation-log",
      name: "MonitoringOperationLog",
      component: () => import("@/views/monitoring/operation-log/index.vue"),
      meta: { icon: "ri:history-line", title: $t("menus.pureMonitoringOperationLog"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/log-search",
      name: "MonitoringLogSearch",
      component: () => import("@/views/monitoring/log-search/index.vue"),
      meta: { icon: "ri:search-line", title: $t("menus.pureMonitoringLogSearch"), roles: MONITORING_ROLES }
    },
    {
      path: "/monitoring/alerts",
      name: "MonitoringAlerts",
      component: () => import("@/views/monitoring/alerts/index.vue"),
      meta: { icon: "ri:alarm-warning-line", title: $t("menus.pureMonitoringAlerts"), roles: MONITORING_ROLES }
    }
  ]
};
