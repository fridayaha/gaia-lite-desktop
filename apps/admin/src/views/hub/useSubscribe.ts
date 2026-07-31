/**
 * 订阅能力到智能体模版 — 列表页与详情页共用。
 *
 * 链路：hub capability (skill) → manager 模版 skill_config → fan-out 实例 Pod。
 * 订阅后由模版 publish 新版本 + instance upgrade 完成版本化生效。
 *
 * 仅 skill 类型支持订阅（manager 侧只有 skills/install-from-hub 入口）。
 */
import { ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useI18n } from "vue-i18n";
import { installFromHubApi, type HubItem } from "@/api/hub";
import {
  getDefinitionsApi,
  publishDefinitionApi,
  type AgentDefinitionResponse,
} from "@/api/manager/agentDefinitions";

export function useSubscribe() {
  const { t } = useI18n();

  const subscribeVisible = ref(false);
  const subscribeTarget = ref<HubItem | null>(null);
  const templates = ref<AgentDefinitionResponse[]>([]);
  const templateLoading = ref(false);
  const selectedTemplateId = ref("");
  const subscribing = ref(false);

  // 已订阅记录：itemId → templateId[]（前端展示用，真实状态以模版 skill_config 为准）
  const subscribedMap = ref<Record<string, string[]>>({});

  async function openSubscribe(item: HubItem) {
    subscribeTarget.value = item;
    selectedTemplateId.value = "";
    subscribeVisible.value = true;
    templateLoading.value = true;
    try {
      const res = await getDefinitionsApi({ page: 1, page_size: 100 });
      templates.value = res.items || [];
    } catch {
      templates.value = [];
      ElMessage.error(t("hub.subscribe.loadTemplatesFailed"));
    } finally {
      templateLoading.value = false;
    }
  }

  function getSubscribedTemplateIds(itemId: string): string[] {
    return subscribedMap.value[itemId] || [];
  }

  const isAlreadySubscribed = () => {
    const target = subscribeTarget.value;
    if (!target || !selectedTemplateId.value) return false;
    return getSubscribedTemplateIds(target.id).includes(selectedTemplateId.value);
  };

  async function doSubscribe() {
    const target = subscribeTarget.value;
    if (!target) return;
    if (!selectedTemplateId.value) {
      ElMessage.warning(t("hub.subscribe.selectAgent"));
      return;
    }
    if (!target.current_version_id) {
      ElMessage.warning(t("hub.subscribe.noVersion"));
      return;
    }
    subscribing.value = true;
    try {
      await installFromHubApi(selectedTemplateId.value, {
        hub_item_id: target.id,
        version_id: target.current_version_id,
      });
      if (!subscribedMap.value[target.id]) subscribedMap.value[target.id] = [];
      if (!subscribedMap.value[target.id].includes(selectedTemplateId.value)) {
        subscribedMap.value[target.id].push(selectedTemplateId.value);
      }
      subscribeVisible.value = false;
      const tpl = templates.value.find(t2 => t2.id === selectedTemplateId.value);
      ElMessage.success(t("hub.subscribe.successMsg", { name: target.name, agent: tpl?.name || "" }));
      // 引导：模版发版 + 实例热更新生效
      await offerPublishAndUpgrade(selectedTemplateId.value, tpl);
    } catch {
      ElMessage.error(t("hub.subscribe.failed"));
    } finally {
      subscribing.value = false;
    }
  }

  async function offerPublishAndUpgrade(templateId: string, tpl?: AgentDefinitionResponse) {
    try {
      await ElMessageBox.confirm(
        t("hub.subscribe.publishHint"),
        t("hub.subscribe.publishTitle"),
        { confirmButtonText: t("hub.subscribe.publishNow"), cancelButtonText: t("common.action.later"), type: "info" }
      );
    } catch {
      // 用户选择稍后，不阻塞
      return;
    }
    try {
      const ver = await publishDefinitionApi(templateId, {
        change_log: t("hub.subscribe.publishChangeLog", { name: tpl?.name || "" }),
      });
      ElMessage.success(t("hub.subscribe.published"));
      // 提示去实例页热更新（不在订阅流程内自动 upgrade，避免影响运行中实例）
      if (tpl && tpl.instance_count > 0) {
        ElMessage.info(t("hub.subscribe.upgradeHint"));
      }
      void ver;
    } catch {
      ElMessage.error(t("hub.subscribe.publishFailed"));
    }
  }

  return {
    subscribeVisible,
    subscribeTarget,
    templates,
    templateLoading,
    selectedTemplateId,
    subscribing,
    subscribedMap,
    openSubscribe,
    doSubscribe,
    getSubscribedTemplateIds,
    isAlreadySubscribed,
  };
}
