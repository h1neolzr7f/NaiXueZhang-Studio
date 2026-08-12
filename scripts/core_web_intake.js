(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  async function request(path, options = {}) { return window.ApiClient.request(path, options); }
  function message(text, ok) {
    const target = byId("accountMessage");
    target.textContent = text;
    target.className = `action-message ${ok ? "ok" : "fail"}`;
  }
  async function loadAccounts() {
    const payload = await request("/api/pixiv/accounts");
    const select = byId("accountSelect");
    select.replaceChildren();
    for (const account of payload.accounts || []) {
      const option = document.createElement("option");
      option.value = account.id;
      option.textContent = `${account.label}${account.has_token ? " ✓" : ""}`;
      if (payload.active_account && payload.active_account.id === account.id) option.selected = true;
      select.append(option);
    }
    byId("pixivAccountId").value = select.value || "";
  }
  byId("accountAdd").addEventListener("click", async () => {
    try {
      await request("/api/pixiv/accounts", { method: "POST", body: JSON.stringify({ label: byId("accountLabel").value, refresh_token: byId("accountToken").value }) });
      byId("accountToken").value = "";
      await loadAccounts();
      message("账号已加密保存在本机。", true);
    } catch (error) { message(error.message || String(error), false); }
  });
  byId("accountSwitch").addEventListener("click", async () => {
    try {
      await request("/api/pixiv/accounts/switch", { method: "POST", body: JSON.stringify({ account_id: byId("accountSelect").value }) });
      byId("pixivAccountId").value = byId("accountSelect").value;
      message("当前账号已切换。", true);
    } catch (error) { message(error.message || String(error), false); }
  });
  byId("accountTest").addEventListener("click", async () => {
    try {
      const payload = await request("/api/pixiv/auth/test", { method: "POST", body: JSON.stringify({ account_id: byId("accountSelect").value }) });
      message(payload.message || (payload.ok ? "登录有效" : "登录无效"), payload.ok === true);
    } catch (error) { message(error.message || String(error), false); }
  });
  byId("accountSelect").addEventListener("change", () => { byId("pixivAccountId").value = byId("accountSelect").value; });
  loadAccounts().catch((error) => message(error.message || String(error), false));
})();
