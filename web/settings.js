(function () {
  const $ = (id) => document.getElementById(id);

  async function api(path, opts) {
    if (!window.ApiClient) throw new Error("ApiClient is not loaded");
    return window.ApiClient.request(path, opts || {});
  }

  function setStatus(text, ok) {
    const el = $("settingsStatus");
    if (!el) return;
    el.textContent = text || "";
    el.className = "settings-status" + (text ? ` show ${ok ? "ok" : "err"}` : "");
  }

  function numberValue(id, fallback) {
    const value = Number($(id)?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function markReady(key, ready, warning) {
    const card = document.querySelector(`[data-ready="${key}"]`);
    if (!card) return;
    card.classList.toggle("ready", !!ready);
    card.classList.toggle("warning", !ready && !!warning);
    card.classList.toggle("missing", !ready && !warning);
  }

  function fillConfig(data) {
    const prefs = data.prefs || {};
    const ai = data.ai || (data.config && data.config.ai) || {};
    $("assistantName").value = prefs.assistant_name || "小镜";
    $("assistantLive2d").checked = prefs.assistant_live2d_enabled !== false;
    $("assistantLive2dModel").value = prefs.assistant_live2d_model || "/assets/vendor/live2d-models/hiyori/Hiyori.model3.json";
    $("assistantPollMode").value = prefs.assistant_poll_mode || "eco";
    $("prefNaiOnly").checked = prefs.nai_only_gallery !== false;
    $("prefQuickStudio").checked = !!prefs.quick_send_studio;
    $("prefShowOther").checked = !!prefs.show_other_ai_types;
    $("prefOptimizeMode").value = prefs.default_optimize_mode || "smart";
    $("aiProvider").value = ai.provider || "DeepSeek";
    $("aiBase").value = ai.api_base || "";
    $("aiModel").value = ai.model || "";
    $("aiTimeout").value = String(ai.timeout || 60);
    $("aiTemperature").value = String(ai.temperature ?? 0.3);
    $("aiMaxTokens").value = String(ai.max_tokens || 4096);
  }

  function renderStatus(data, pixiv) {
    const ready = data.ready || {};
    markReady("gallery", ready.gallery !== false);
    markReady("chat", !!ready.ai_key);
    markReady("generate", !!ready.nai_token);
    const publishReady = !!(
      pixiv
      && pixiv.active_account
      && pixiv.active_account.has_token
    );
    markReady("publish", publishReady, !publishReady);
    const token = data.token || {};
    $("tokenStatusText").textContent = token.has_token
      ? `已配置 ${token.count || 0} 个槽位`
      : "未配置 Token，生成暂不可用";
    $("tokenEmptyState").hidden = !!token.has_token;
  }

  function renderUsage(data) {
    const summary = data.summary || {};
    $("usageCalls").textContent = Number(summary.calls || 0).toLocaleString("zh-CN");
    $("usageTotalTokens").textContent = Number(summary.total_tokens || 0).toLocaleString("zh-CN");
    $("usageImages").textContent = Number(summary.images || 0).toLocaleString("zh-CN");
    $("usageAnlas").textContent = summary.anlas_complete
      ? Number(summary.anlas_spent || 0).toLocaleString("zh-CN")
      : "部分未知";
    $("usageNote").textContent = summary.anlas_complete
      ? "Anlas 记录完整。"
      : `${Number(summary.anlas_unknown_images || 0)} 张图片未返回明确 Anlas 消耗，账本不会猜测费用。`;
  }

  function renderKnowledge(data) {
    const state = $("knowledgeState");
    const value = data.state || (data.usable ? "ready" : "empty");
    state.textContent = value === "ready" ? "可用" : value;
    state.dataset.state = value;
    $("knowledgeDocuments").textContent = Number(data.documents || 0).toLocaleString("zh-CN");
    $("knowledgeChunks").textContent = Number(data.chunks || 0).toLocaleString("zh-CN");
    $("knowledgeIndexVersion").textContent = data.index_version || "—";
    $("knowledgeLastSuccess").textContent = data.last_success_at
      ? String(data.last_success_at).replace("T", " ").slice(0, 19)
      : "—";
    $("knowledgeMessage").textContent = data.last_error || "";
    $("knowledgeEmptyState").hidden = !!data.usable || Number(data.documents || 0) > 0;
  }

  async function load() {
    const [config, status, usage, knowledge, pixiv] = await Promise.all([
      api("/api/settings/config"),
      api("/api/settings/status"),
      api("/api/settings/usage").catch(() => ({ summary: {} })),
      api("/api/settings/knowledge").catch(() => ({ state: "unavailable" })),
      api("/api/pixiv/config").catch(() => ({})),
    ]);
    fillConfig(config);
    renderStatus(status, pixiv);
    renderUsage(usage);
    renderKnowledge(knowledge);
  }

  function prefsPayload() {
    return {
      assistant_name: ($("assistantName").value || "小镜").trim().slice(0, 12) || "小镜",
      assistant_live2d_enabled: $("assistantLive2d").checked,
      assistant_live2d_model: $("assistantLive2dModel").value.trim(),
      assistant_poll_mode: $("assistantPollMode").value,
      nai_only_gallery: $("prefNaiOnly").checked,
      quick_send_studio: $("prefQuickStudio").checked,
      show_other_ai_types: $("prefShowOther").checked,
      default_optimize_mode: $("prefOptimizeMode").value,
    };
  }

  function aiPayload() {
    return {
      provider: $("aiProvider").value,
      api_base: $("aiBase").value.trim(),
      model: $("aiModel").value.trim(),
      api_key: $("settingsAiKey").value.trim(),
      timeout: numberValue("aiTimeout", 60),
      temperature: numberValue("aiTemperature", 0.3),
      max_tokens: numberValue("aiMaxTokens", 4096),
    };
  }

  $("saveAllBtn")?.addEventListener("click", async () => {
    const button = $("saveAllBtn");
    button.disabled = true;
    try {
      await api("/api/settings/config", {
        method: "POST",
        body: { prefs: prefsPayload(), ai: aiPayload() },
      });
      $("settingsAiKey").value = "";
      setStatus("全部配置已保存，管家、工作台和发布页会统一使用", true);
      await load();
    } catch (error) {
      setStatus(error.message || String(error), false);
    } finally {
      button.disabled = false;
    }
  });

  $("saveTokenBtn")?.addEventListener("click", async () => {
    try {
      let token = ($("settingsToken").value || "").trim();
      const defaultProvider = $("tokenDefaultProvider").value;
      if (defaultProvider && token) {
        token = token.split(/\r?\n/).map((line) => {
          const value = line.trim();
          if (!value || value.startsWith("{") || value.includes(":")) return line;
          if (defaultProvider === "xianyun" && !/^pst-/i.test(value)) return `xianyun:${value}`;
          if (defaultProvider === "novelai" && !/^(xianyun|xy|idlecloud):/i.test(value)) {
            return value.startsWith("pst-") ? value : `nai:${value}`;
          }
          return line;
        }).join("\n");
      }
      await api("/api/settings/nai-token", {
        method: "POST",
        body: { token, default_provider: defaultProvider || undefined },
      });
      $("settingsToken").value = "";
      setStatus("生图账号池已保存", true);
      await load();
    } catch (error) {
      setStatus(error.message || String(error), false);
    }
  });

  $("checkTokenBtn")?.addEventListener("click", async () => {
    try {
      const result = await api("/api/nai/token/check", {
        method: "POST",
        body: { remove_bad: true },
      });
      setStatus((result.results || []).map((item) => item.message || item.label).join(" · ") || "检查完成", true);
      await load();
    } catch (error) {
      setStatus(error.message || String(error), false);
    }
  });

  $("testAiBtn")?.addEventListener("click", async () => {
    try {
      const result = await api("/api/settings/ai-test", { method: "POST", body: {} });
      setStatus(result.message || `文本能力可用：${result.model || "当前模型"}`, true);
    } catch (error) {
      setStatus(error.message || String(error), false);
    }
  });

  $("testVisionBtn")?.addEventListener("click", async () => {
    try {
      const result = await api("/api/settings/ai-vision-test", { method: "POST", body: {} });
      setStatus(result.message || `视觉能力${result.vision_confirmed ? "已确认" : "未确认"}`, !!result.ok);
    } catch (error) {
      setStatus(error.message || String(error), false);
    }
  });

  $("loadAiModelsBtn")?.addEventListener("click", async () => {
    try {
      const result = await api("/api/settings/ai-models");
      const list = $("aiModelOptions");
      list.innerHTML = "";
      (result.models || []).forEach((model) => {
        const option = document.createElement("option");
        option.value = String(model);
        list.appendChild(option);
      });
      setStatus(`已读取 ${Number(result.count ?? (result.models || []).length)} 个模型`, true);
    } catch (error) {
      setStatus(error.message || String(error), false);
    }
  });

  $("knowledgeRebuildBtn")?.addEventListener("click", async () => {
    const button = $("knowledgeRebuildBtn");
    button.disabled = true;
    try {
      const result = await api("/api/settings/knowledge/rebuild", { method: "POST", body: {} });
      const link = document.createElement("a");
      link.href = result.task_url || "/butler#taskCenter";
      link.textContent = "已提交，打开管家任务中心";
      const host = $("knowledgeMessage");
      host.textContent = "";
      host.appendChild(link);
      setStatus("知识库重建任务已提交", true);
    } catch (error) {
      setStatus(error.message || String(error), false);
    } finally {
      button.disabled = false;
    }
  });

  async function loadPixivIntake() {
    const host = $("pixivIntakeSummary");
    if (!host) return;
    try {
      const payload = await api("/api/crawler/pixiv/task");
      const task = payload.task || {};
      const mode = task.source_mode || "auto";
      const channel =
        mode === "public" ? "公网页面" :
        mode === "api" ? "Pixiv App API" :
        "自动（有账号 API / 无账号公网）";
      const enabled = task.enabled ? "已启用" : "未启用";
      const delay = Number(task.request_delay_sec ?? 0);
      const proxy = String(task.proxy_url || "").trim();
      const browser = task.browser_mode ? "浏览器渲染开" : "浏览器渲染关";
      const account = String(task.account_id || "").trim() || "（未指定）";
      const lines = [
        `状态：${enabled} · 通道：${channel} · 浏览器：${browser}`,
        `请求间隔：${delay} 秒 · 代理：${proxy || "未配置"}`,
        `账号槽：${account} · 关键词：${(task.scopes || []).filter((x) => x.type === "search").length} 个搜索 / ${(task.scopes || []).filter((x) => x.type === "user").length} 个画师 / ${(task.scopes || []).filter((x) => x.type === "ranking").length} 个榜单`,
      ];
      host.textContent = lines.join("\n");
    } catch (error) {
      host.textContent = `读取失败：${error.message || error}`;
    }
  }

  async function loadUpdate() {
    const host = $("updateCurrent");
    const status = $("updateStatus");
    const checkBtn = $("updateCheckBtn");
    const downloadBtn = $("updateDownloadBtn");
    if (!host || !checkBtn) return;
    let latest = "";
    checkBtn.addEventListener("click", async () => {
      checkBtn.disabled = true;
      try {
        const payload = await api("/api/update/check");
        host.textContent = payload.current_version || "-";
        latest = payload.latest_version || "";
        if (payload.update_available) {
          status.textContent = `发现新版本 ${latest}（当前 ${payload.current_version}）`;
          downloadBtn.disabled = false;
        } else {
          status.textContent = "已是最新版本";
          downloadBtn.disabled = true;
        }
      } catch (error) {
        status.textContent = error.message || String(error);
      } finally {
        checkBtn.disabled = false;
      }
    });
    downloadBtn && downloadBtn.addEventListener("click", async () => {
      downloadBtn.disabled = true;
      try {
        const payload = await api("/api/update/download", { method: "POST", body: "{}" });
        status.textContent = payload.message || "更新包已下载";
      } catch (error) {
        status.textContent = error.message || String(error);
      } finally {
        downloadBtn.disabled = false;
      }
    });
  }

  load().then(() => { loadPixivIntake(); loadUpdate(); }).catch((error) => setStatus(error.message || String(error), false));
})();
