// 由 pixiv.js 拆分：数据分析与 AI 配置面板。经典脚本，依赖 pixiv.js 中的全局函数，加载顺序须先于 pixiv.js。

async function runAnalytics(scope) {
  const st = document.getElementById("analyticsStatus");
  const btnA = document.getElementById("runAnalyticsActive");
  const btnB = document.getElementById("runAnalyticsAll");
  btnA.disabled = true;
  btnB.disabled = true;
  setStatus(st, "AI 正在分析数据…");
  try {
    const res = await window.ApiClient.raw("/api/pixiv/analytics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: scope === "all" ? "all" : activeAccountId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "分析失败");
    renderAnalytics(data.analysis, data.generated_at);
    setStatus(st, "分析完成", "ok");
  } catch (e) {
    setStatus(st, e.message, "err");
  } finally {
    btnA.disabled = false;
    btnB.disabled = false;
  }
}

document.getElementById("runAnalyticsActive").addEventListener("click", () => runAnalytics("active"));
document.getElementById("runAnalyticsAll").addEventListener("click", () => runAnalytics("all"));

document.getElementById("testPixiv").addEventListener("click", async () => {
  const btn = document.getElementById("testPixiv");
  const badge = document.getElementById("pixivAuthBadge");
  btn.disabled = true;
  if (badge) {
    badge.className = "px-auth-badge pending";
    badge.textContent = "检测中…";
  }
  setStatus(document.getElementById("pixivAuthStatus"), "正在连接 Pixiv OAuth…");
  try {
    const res = await window.ApiClient.raw("/api/pixiv/auth/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: activeAccountId }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      await loadConfig();
    }
    renderPixivAuth({
      ok: data.ok,
      has_refresh_token: true,
      user: data.user,
      message: data.message,
      error: data.error,
    });
  } catch (e) {
    renderPixivAuth({
      ok: false,
      has_refresh_token: true,
      message: e.message,
      error: { hint: e.message },
    });
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("saveAiKey").addEventListener("click", async () => {
  const key = document.getElementById("aiKey").value.trim();
  if (!key) return alert("请填写 API Key");
  const res = await window.ApiClient.raw("/api/pixiv/ai-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: key }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return alert(data.detail || "保存失败");
  document.getElementById("aiKey").value = "";
  alert("AI Key 已保存");
  await loadConfig();
});

document.getElementById("saveAiCfg").addEventListener("click", async () => {
  const res = await window.ApiClient.raw("/api/pixiv/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ai: {
        provider: document.getElementById("aiProvider").value,
        api_base: document.getElementById("aiBase").value.trim(),
        model: document.getElementById("aiModel").value.trim(),
      },
      account: {
        direction: document.getElementById("direction").value.trim(),
        nickname_hint: document.getElementById("nicknameHint").value.trim(),
      },
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return alert(data.detail || "保存失败");
  config = data.config;
  if (activeAccountId) {
    await window.ApiClient.raw(`/api/pixiv/accounts/${encodeURIComponent(activeAccountId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction: document.getElementById("direction").value.trim() }),
    });
  }
  alert("配置已保存");
  await loadConfig();
});

document.getElementById("testAiCfg").addEventListener("click", async () => {
  const btn = document.getElementById("testAiCfg");
  const st = document.getElementById("aiAuthStatus");
  btn.disabled = true;
  setStatus(st, "正在测试 AI 配置…");
  try {
    await window.ApiClient.raw("/api/pixiv/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ai: {
          provider: document.getElementById("aiProvider").value,
          api_base: document.getElementById("aiBase").value.trim(),
          model: document.getElementById("aiModel").value.trim(),
        },
      }),
    });
    const res = await window.ApiClient.raw("/api/pixiv/ai-test", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || "AI 测试失败");
    setStatus(st, `AI 可用：${data.provider || ""} / ${data.model || ""}`, "ok");
    await loadConfig();
  } catch (e) {
    setStatus(st, e.message, "err");
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("genPersona").addEventListener("click", async () => {
  const btn = document.getElementById("genPersona");
  btn.disabled = true;
  try {
    const res = await window.ApiClient.raw("/api/pixiv/director/persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        direction: document.getElementById("direction").value.trim(),
        nickname_hint: document.getElementById("nicknameHint").value.trim(),
        save: true,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "生成失败");
    personaCache = data.persona || {};
    document.getElementById("personaBox").value = JSON.stringify(personaCache, null, 2);
    if (personaCache.warning) alert(personaCache.warning);
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("genPost").addEventListener("click", () => generateCopy(false));
