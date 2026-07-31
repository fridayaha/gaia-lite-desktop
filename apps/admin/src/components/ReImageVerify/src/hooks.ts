import { ref, onMounted } from "vue";
import { http } from "@/utils/http";

/**
 * 后端生成的图形验证码。
 *
 * 调用 `GET /api/manager/auth/captcha` 拿 `captcha_id` + base64 PNG 图片。
 * 5min 有效，1 次性使用 — 校验错即失效，需重新获取。
 *
 * 父组件通过 `v-model:captcha-id` 拿到 captchaId，连同用户输入的 answer 一起提交。
 * 校验失败后调 `refresh()` 重新获取。
 */
export const useImageVerify = () => {
  const imgCode = ref("");
  const captchaId = ref("");
  const loading = ref(false);

  async function refresh() {
    if (loading.value) return;
    loading.value = true;
    try {
      const res = await http.request<
        { captcha_id: string; image_base64: string }
      >("get", "/api/manager/auth/captcha");
      captchaId.value = res.captcha_id;
      imgCode.value = res.image_base64;
    } finally {
      loading.value = false;
    }
  }

  onMounted(() => {
    refresh();
  });

  return {
    imgCode,
    captchaId,
    refresh,
    loading
  };
};
