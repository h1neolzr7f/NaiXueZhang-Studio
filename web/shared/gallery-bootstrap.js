(function () {
  const CONFIG_KEY = "aitag.config.cache.v1";
  const CONFIG_TTL_MS = 5 * 60 * 1000;
  const SETUP_KEY = "aitag.setup.cache.v1";
  const SETUP_TTL_MS = 2 * 60 * 1000;

  function readJson(key, ttlMs) {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      if (Date.now() - Number(parsed.at || 0) > ttlMs) return null;
      return parsed.data || null;
    } catch {
      return null;
    }
  }

  function writeJson(key, data) {
    try {
      sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), data }));
    } catch { /* ignore quota */ }
  }

  async function loadConfig(apiBase, opts) {
    const force = !!(opts && opts.force);
    if (!force) {
      const cached = readJson(CONFIG_KEY, CONFIG_TTL_MS);
      if (cached) return cached;
    }
    const data = await ApiClient.get("/api/config");
    writeJson(CONFIG_KEY, data);
    return data;
  }

  function applySetupBanner(status) {
    const banner = document.getElementById("setupBanner");
    const text = document.getElementById("setupBannerText");
    if (!banner || !text) return;
    const ready = status && status.ready;
    const missing = [];
    if (ready && !ready.nai_token) missing.push("NAI/闲云 Token");
    if (ready && !ready.ai_key) missing.push("智能优化 Key");
    if (!missing.length) {
      banner.classList.add("hidden");
      return;
    }
    text.textContent = `首次使用：请配置 ${missing.join("、")}`;
    banner.classList.remove("hidden");
  }

  async function refreshSetupBanner(apiBase, opts) {
    const force = !!(opts && opts.force);
    if (!force) {
      const cached = readJson(SETUP_KEY, SETUP_TTL_MS);
      if (cached) {
        applySetupBanner(cached);
        return cached;
      }
    }
    const data = await ApiClient.get("/api/settings/status").catch(() => null);
    if (!data) return null;
    writeJson(SETUP_KEY, data);
    applySetupBanner(data);
    return data;
  }

  window.GalleryBootstrap = {
    loadConfig,
    refreshSetupBanner,
    invalidateConfig() {
      try { sessionStorage.removeItem(CONFIG_KEY); } catch { }
    },
    invalidateSetup() {
      try { sessionStorage.removeItem(SETUP_KEY); } catch { }
    },
  };
})();