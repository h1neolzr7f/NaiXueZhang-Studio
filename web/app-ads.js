// 画廊广告栈：默认 CONFIG.ads_enabled=false，由 app-core.js 在开启时按需注入本脚本。
// 作为 classic script 注入，与 app-core.js 共享全局词法作用域（CONFIG/state/galleryEl 等）。
(function () {
  function currentAdDevice() {
    return window.innerWidth <= MOBILE_MAX_WIDTH ? 'mobile' : 'desktop';
  }

  function getAdVariants(placement = 'search') {
    if (!adsEnabled()) return [];
    const device = currentAdDevice();
    const fallbackDevice = device === 'mobile' ? 'desktop' : 'mobile';
    const targetLocation = placement === 'detail' ? 'detail' : 'search';
    const variants = [];
    CONFIG.ads.forEach((group) => {
      if (!group || typeof group !== 'object') return;
      const locations = Array.isArray(group.locations) && group.locations.length ? group.locations : ['all'];
      if (!(locations.includes('all') || locations.includes(targetLocation))) return;
      const groupId = String(group.id || group.name || 'media').trim();
      const name = String(group.name || groupId).trim() || groupId;
      const href = String(group.href || '').trim();
      if (!href) return;
      const deviceConfig = group[device] || group[fallbackDevice] || {};
      const images = Array.isArray(deviceConfig.images) ? deviceConfig.images : [];
      images.forEach((image, index) => {
        if (!image || typeof image !== 'object') return;
        const src = String(image.src || '').trim();
        if (!src) return;
        const width = Number(image.width || 0) > 0 ? Number(image.width) : 0;
        const height = Number(image.height || 0) > 0 ? Number(image.height) : 0;
        variants.push({
          key: `${device}:${groupId}:${src}:${index}`,
          groupId,
          name,
          href,
          src,
          width,
          height,
        });
      });
    });
    return variants;
  }

  function chooseAdVariant(placement = 'search') {
    const variants = getAdVariants(placement);
    if (!variants.length) return null;
    const device = currentAdDevice();
    const keyName = `gallery_ad_last_v1_${placement}_${device}`;
    let lastKey = state.ads.lastKey || '';
    try { lastKey = lastKey || localStorage.getItem(keyName) || ''; } catch { }
    let pool = variants.filter((v) => v.key !== lastKey);
    if (!pool.length) pool = variants;
    const picked = pool[Math.floor(Math.random() * pool.length)] || pool[0] || null;
    if (picked) {
      state.ads.lastKey = picked.key;
      try { localStorage.setItem(keyName, picked.key); } catch { }
    }
    return picked;
  }

  function shouldPreloadAdsOnSearchPage() {
    try {
      if (state.directDetail) return false;
      if (String(window.location.pathname || '').startsWith('/i/')) return false;
    } catch { }
    return true;
  }

  function getAdPreloadUrlsForCurrentPage() {
    if (!adsEnabled()) return [];
    const map = new Map();
    try {
      [...getAdVariants('search'), ...getAdVariants('detail')].forEach((ad) => {
        const src = String((ad && ad.src) || '').trim();
        if (src && !map.has(src)) map.set(src, src);
      });
    } catch { }
    return Array.from(map.values());
  }

  function scheduleSearchAdPreload() {
    if (!shouldPreloadAdsOnSearchPage()) return;
    const urls = getAdPreloadUrlsForCurrentPage().filter((src) => {
      try { return !state.ads.preloaded.has(src); } catch { return true; }
    });
    if (!urls.length) return;
    if (state.ads.preloadTimer) return;

    const run = () => {
      state.ads.preloadTimer = 0;
      urls.forEach((src, index) => {
        setTimeout(() => {
          try {
            if (state.ads.preloaded.has(src)) return;
            state.ads.preloaded.add(src);
            const img = new Image();
            img.decoding = 'async';
            try { img.fetchPriority = 'low'; } catch { }
            img.src = src;
          } catch { }
        }, index * 140);
      });
    };

    state.ads.preloadTimer = setTimeout(run, 120);
  }

  function createAdElement(placement = 'list') {
    const ad = chooseAdVariant(placement === 'detail' ? 'detail' : 'search');
    if (!ad) return null;
    const slot = document.createElement('div');
    slot.className = placement === 'detail' ? 'media-insert detail-insert' : 'media-insert gallery-insert';
    slot.dataset.insertGroup = ad.groupId;

    const link = document.createElement('a');
    link.className = 'media-insert-link';
    link.href = ad.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.title = ad.name;

    const img = document.createElement('img');
    img.className = 'media-insert-image';
    img.loading = 'eager';
    img.fetchPriority = 'high';
    img.decoding = 'async';
    img.alt = ad.name;
    img.src = ad.src;
    if (ad.width) {
      img.style.width = `${ad.width}px`;
    }
    if (ad.width && ad.height) {
      img.style.aspectRatio = `${ad.width} / ${ad.height}`;
    }
    link.appendChild(img);
    slot.appendChild(link);
    return slot;
  }

  function appendGalleryAd(slotKey) {
    if (!adsEnabled() || !galleryEl || !slotKey) return;
    if (galleryEl.querySelector(`[data-insert-slot="${slotKey}"]`)) return;
    const adEl = createAdElement('list');
    if (!adEl) return;
    adEl.dataset.insertSlot = slotKey;
    galleryEl.appendChild(adEl);
  }

  window.GalleryAds = { appendGalleryAd, createAdElement, scheduleSearchAdPreload };
})();
