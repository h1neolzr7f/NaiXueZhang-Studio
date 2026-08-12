(function () {
  const loaded = new Map();

  function load(src) {
    const url = String(src || "").trim();
    if (!url) return Promise.resolve();
    if (loaded.has(url)) return loaded.get(url);
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = url;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`failed to load ${url}`));
      document.head.appendChild(script);
    });
    loaded.set(url, promise);
    return promise;
  }

  async function ensureDetailDeps() {
    await Promise.all([
      load("/assets/tag_i18n.js?v=cadf4820cd"),
      load("/assets/nai.js?v=a7a56c84c0"),
      load("/assets/nai_x.js?v=c773de6f37"),
    ]);
    if (window.TagI18n && typeof window.TagI18n.load === "function" && !window.TagI18n.ready) {
      await window.TagI18n.load();
    }
  }

  window.DeferredScripts = { load, ensureDetailDeps };
})();