// 共享 HTML 转义。window 属性而非顶层 const：各页面可按需覆盖，
// 且不会与 app-core.js 的顶层 const escapeHtml 产生重复声明冲突。
window.escapeHtml =
  window.escapeHtml ||
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  };

// 只允许 http(s) 链接进入 href，其余协议（javascript: 等）一律 neutralize。
window.safeHttpUrl =
  window.safeHttpUrl ||
  function safeHttpUrl(url, fallback = "#") {
    const s = String(url || "").trim();
    return /^https?:\/\//i.test(s) ? s : fallback;
  };
