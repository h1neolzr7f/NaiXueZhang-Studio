// 由 pixiv.js 拆分：登录认证标签页与浏览器登录。经典脚本，依赖 pixiv.js 中的全局函数，加载顺序须先于 pixiv.js。

function setAuthTab(mode) {
  const passkey = mode === "passkey";
  const email = mode === "email";
  const token = mode === "token";
  document.getElementById("authTabPasskey").classList.toggle("active", passkey);
  document.getElementById("authTabEmail").classList.toggle("active", email);
  document.getElementById("authTabToken").classList.toggle("active", token);
  document.getElementById("authPasskeyPane").hidden = !passkey;
  document.getElementById("authEmailPane").hidden = !email;
  document.getElementById("authTokenPane").hidden = !token;
}
document.getElementById("authTabPasskey").addEventListener("click", () => setAuthTab("passkey"));
document.getElementById("authTabEmail").addEventListener("click", () => setAuthTab("email"));
document.getElementById("authTabToken").addEventListener("click", () => setAuthTab("token"));

async function runBrowserLogin(endpoint, statusText) {
  const btn = document.getElementById(endpoint === "browser" ? "passkeyLoginPixiv" : "emailLoginPixiv");
  const badge = document.getElementById("pixivAuthBadge");
  btn.disabled = true;
  if (badge) {
    badge.className = "px-auth-badge pending";
    badge.textContent = "登录中…";
  }
  setStatus(document.getElementById("pixivAuthStatus"), statusText);
  try {
    const body = { account_id: activeAccountId, label: document.getElementById("accLabel").value.trim(), direction: document.getElementById("direction").value.trim() };
    if (endpoint === "email") {
      body.username = document.getElementById("pxEmail").value.trim();
      body.password = document.getElementById("pxPassword").value;
    }
    const res = await window.ApiClient.raw(`/api/pixiv/auth/${endpoint}-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || "登录失败");
    if (endpoint === "email") document.getElementById("pxPassword").value = "";
    renderPixivAuth({
      ok: data.ok,
      has_refresh_token: true,
      user: (data.auth && data.auth.user) || data.user,
      message: data.message,
      error: data.error,
    });
    await loadConfig();
    setStatus(document.getElementById("pixivAuthStatus"), data.ok ? (data.message || "登录成功") : ((data.error && data.error.hint) || data.message || "登录失败"), data.ok ? "ok" : "err");
    toast(data.ok ? (data.message || "登录成功") : ((data.error && data.error.hint) || data.message || "登录失败"), data.ok ? "ok" : "err");
    refreshReadyStrip();
  } catch (e) {
    renderPixivAuth({ ok: false, has_refresh_token: true, message: e.message, error: { hint: e.message } });
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}
document.getElementById("passkeyLoginPixiv").addEventListener("click", async () => {
  if (!activeAccountId) {
    // Auto-create slot so first-time setup is one click
    try {
      const res = await window.ApiClient.raw("/api/pixiv/accounts/slot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label: (document.getElementById("accLabel")?.value || "").trim() || "主号",
          direction: (document.getElementById("direction")?.value || "").trim(),
          set_active: true,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) await loadConfig();
    } catch (_) { /* ignore and continue */ }
  }
  runBrowserLogin("browser", "正在打开浏览器，请用通行密钥登录 Pixiv（最多 5 分钟）…");
});

document.getElementById("emailLoginPixiv").addEventListener("click", () => {
  const email = document.getElementById("pxEmail").value.trim();
  const password = document.getElementById("pxPassword").value;
  if (!email || !password) return alert("请填写 Pixiv 邮箱/ID 和密码");
  runBrowserLogin("email", "正在弹出浏览器登录 Pixiv，最多等待 5 分钟…");
});
document.getElementById("testPixiv2").addEventListener("click", () => document.getElementById("testPixiv").click());
document.getElementById("testPixivEmail").addEventListener("click", () => document.getElementById("testPixiv").click());
document.getElementById("delAccount2").addEventListener("click", () => document.getElementById("delAccount").click());
document.getElementById("delAccountEmail").addEventListener("click", () => document.getElementById("delAccount").click());

document.getElementById("togglePxToken").addEventListener("click", () => {
  const input = document.getElementById("pxRefresh");
  const btn = document.getElementById("togglePxToken");
  const hidden = input.type === "password";
  input.type = hidden ? "text" : "password";
  btn.textContent = hidden ? "隐藏" : "显示";
});

document.getElementById("delAccount").addEventListener("click", async () => {
  if (!activeAccountId) return alert("没有可删除的账号");
  if (!confirm("确定删除当前账号？仅删本地配置，不影响 Pixiv 账号本身。")) return;
  const res = await window.ApiClient.raw(`/api/pixiv/accounts/${encodeURIComponent(activeAccountId)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return alert(data.detail || "删除失败");
  await loadConfig();
});

function bindUpscaleStepper() {
  const input = document.getElementById("pipeUpscaleScale");
  const minus = document.getElementById("pipeUpscaleMinus");
  const plus = document.getElementById("pipeUpscalePlus");
  if (!input || !minus || !plus) return;
  const clamp = () => {
    let v = Number(input.value) || 2;
    v = Math.max(1, Math.min(4, Math.round(v)));
    input.value = String(v);
    return v;
  };
  minus.addEventListener("click", () => {
    input.value = String(Math.max(1, clamp() - 1));
  });
  plus.addEventListener("click", () => {
    input.value = String(Math.min(4, clamp() + 1));
  });
  input.addEventListener("change", clamp);
}
bindUpscaleStepper();

document.getElementById("refreshStats").addEventListener("click", async () => {
  const btn = document.getElementById("refreshStats");
  btn.disabled = true;
  setStatus(document.getElementById("statsMeta"), "正在拉取各账号数据…");
  try {
    const res = await window.ApiClient.raw("/api/pixiv/stats/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: true }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "刷新失败");
    statsData = { ...(statsData || {}), ...data, items: data.items || [] };
    renderStats();
    if ((data.errors || []).length) {
      alert("部分账号刷新失败：\n" + data.errors.map((e) => e.message).join("\n"));
    }
  } catch (e) {
    setStatus(document.getElementById("statsMeta"), e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

