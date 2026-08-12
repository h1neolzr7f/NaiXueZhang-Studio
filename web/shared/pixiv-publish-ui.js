(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.PixivPublishUI = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function present(value, fallback) {
    const text = String(value == null ? "" : value).trim();
    return text || fallback;
  }

  function buildConfirmation(summary) {
    const value = summary || {};
    const tags = Array.isArray(value.tags)
      ? value.tags.map((tag) => present(tag, "")).filter(Boolean)
      : [];
    const imageCount = Math.max(1, Number(value.imageCount) || 1);
    return [
      "请核对以下发布信息：",
      `动作：${present(value.action, "发布")}`,
      `账号：${present(value.account, "未选择")}`,
      `图片：${imageCount} 张`,
      `标题：${present(value.title, "（空）")}`,
      `Tags：${tags.length ? tags.join(" / ") : "（无）"}`,
      `分级：${present(value.rating, "未确认")}`,
      `后处理：${present(value.pipeline, "未确认")}`,
      "确认继续发布到 Pixiv？",
    ].join("\n");
  }

  function setBusy(buttons, busy) {
    (buttons || []).filter(Boolean).forEach((button) => {
      button.disabled = Boolean(busy);
      if (busy) button.setAttribute("aria-busy", "true");
      else button.removeAttribute("aria-busy");
    });
  }

  function createSubmissionGuard() {
    let locked = false;
    return {
      tryAcquire() {
        if (locked) return false;
        locked = true;
        return true;
      },
      release() {
        locked = false;
      },
      isLocked() {
        return locked;
      },
    };
  }

  return { buildConfirmation, setBusy, createSubmissionGuard };
});
