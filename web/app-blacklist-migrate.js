// 旧域名黑名单迁移：默认 CONFIG.old_blacklist_migrate_enabled=false，
// 由 app-core.js 在开启时按需注入本脚本。与 app-core.js 共享全局词法作用域。
(function () {
  function _normalizeOrigin(urlStr) {
    try {
      let s = String(urlStr || '').trim();
      if (!s) return '';
      if (!s.includes('://')) s = `https://${s}`;
      const u = new URL(s);
      return u.origin;
    } catch {
      return '';
    }
  }

  function ensureImportOldBlacklistButton(oldOrigin) {
    if (!oldBlacklistMigrationEnabled()) return null;
    try {
      const host = document.getElementById('fcExtraSettings') || fcPanel;
      if (!host) return null;
      try {
        const existing = document.getElementById('importOldBlacklistBtn');
        if (existing && existing !== importOldBlacklistBtn) {
          try { existing.remove(); } catch { }
        }
      } catch { }
      if (importOldBlacklistBtn) {
        try {
          if (importOldBlacklistBtn.parentNode !== host) host.appendChild(importOldBlacklistBtn);
        } catch { }
        return importOldBlacklistBtn;
      }
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.id = 'importOldBlacklistBtn';
      btn.className = 'btn outline';
      btn.textContent = t('btn_import_blacklist');
      btn.addEventListener('click', async () => {
        try {
          const ok = await migrateBlacklistFromOldDomainViaPopup(oldOrigin);
          if (!importOldBlacklistBtn) return;
          if (ok) {
            importOldBlacklistBtn.textContent = t('import_blacklist_done');
            setTimeout(() => { try { if (importOldBlacklistBtn) importOldBlacklistBtn.remove(); } catch { } importOldBlacklistBtn = null; }, 900);
          } else {
            importOldBlacklistBtn.textContent = t('import_blacklist_failed');
            setTimeout(() => { try { if (importOldBlacklistBtn) importOldBlacklistBtn.textContent = t('btn_import_blacklist'); } catch { } }, 1600);
          }
        } catch { }
      });
      try {
        host.appendChild(btn);
      } catch {
        try { host.appendChild(btn); } catch { }
      }
      importOldBlacklistBtn = btn;
      return btn;
    } catch {
      return null;
    }
  }

  async function migrateBlacklistFromOldDomainViaPopup(oldOrigin) {
    if (!oldBlacklistMigrationEnabled()) return false;
    let currentRaw = '';
    try { currentRaw = String(localStorage.getItem('gallery_blacklist') || ''); } catch { currentRaw = ''; }
    if (currentRaw.trim()) return false;

    const btn = importOldBlacklistBtn;
    if (btn) {
      try { btn.disabled = true; } catch { }
      try { btn.textContent = t('import_blacklist_starting'); } catch { }
    }

    const targetOrigin = encodeURIComponent(window.location.origin);
    const url = `${oldOrigin}/api/migrate/blacklist?target_origin=${targetOrigin}`;
    const w = window.open(url, 'migrate_blacklist', 'popup=yes,width=520,height=520');
    if (!w) {
      if (btn) { try { btn.disabled = false; btn.textContent = t('btn_import_blacklist'); } catch { } }
      try { alert(t('import_blacklist_popup_blocked')); } catch { }
      return false;
    }

    return await new Promise((resolve) => {
      let settled = false;
      let timer = null;

      const cleanup = () => {
        try { window.removeEventListener('message', onMsg); } catch { }
        if (timer) { try { clearTimeout(timer); } catch { } timer = null; }
        try { if (w && !w.closed) w.close(); } catch { }
        if (btn) { try { btn.disabled = false; } catch { } }
      };
      const finalize = (ok) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(!!ok);
      };
      const onMsg = (e) => {
        try {
          if (!e || e.origin !== oldOrigin) return;
          const data = e.data || {};
          if (!data || data.type !== 'gallery_blacklist_migrate_v1') return;
          if (!data.ok) return finalize(false);
          const raw = String(data.raw || '');
          if (!raw.trim()) return finalize(false);
          let currentNow = '';
          try { currentNow = String(localStorage.getItem('gallery_blacklist') || ''); } catch { currentNow = ''; }
          if (currentNow.trim()) return finalize(false);
          try { localStorage.setItem('gallery_blacklist', raw); } catch { }
          try { blacklistInput.value = raw; } catch { }
          try { state.blacklist = parseWords(raw).map((x) => x.toLowerCase()); } catch { }
          finalize(true);
        } catch {
          finalize(false);
        }
      };

      try { window.addEventListener('message', onMsg); } catch { }
      timer = setTimeout(() => finalize(false), 15000);
    });
  }

  async function migrateBlacklistFromOldDomainIfNeeded() {
    if (!oldBlacklistMigrationEnabled()) return false;
    let currentRaw = '';
    try { currentRaw = String(localStorage.getItem('gallery_blacklist') || ''); } catch { currentRaw = ''; }
    if (currentRaw.trim()) return false;

    const oldOrigin = _normalizeOrigin(CONFIG.old_domain || '');
    if (!oldOrigin) return false;
    if (oldOrigin === window.location.origin) return false;

    const doneKey = `${BLACKLIST_MIGRATE_DONE_PREFIX}${oldOrigin}`;
    try { if (localStorage.getItem(doneKey) === '1') return false; } catch { }

    const okIframe = await new Promise((resolve) => {
      let settled = false;
      let iframe = null;
      let timer = null;

      const cleanup = () => {
        try { window.removeEventListener('message', onMsg); } catch { }
        if (timer) { try { clearTimeout(timer); } catch { } timer = null; }
        if (iframe) { try { iframe.remove(); } catch { } iframe = null; }
      };
      const finalize = (ok) => {
        if (settled) return;
        settled = true;
        if (ok) {
          try { localStorage.setItem(doneKey, '1'); } catch { }
        }
        cleanup();
        resolve(!!ok);
      };
      const onMsg = (e) => {
        try {
          if (!e || e.origin !== oldOrigin) return;
          const data = e.data || {};
          if (!data || data.type !== 'gallery_blacklist_migrate_v1') return;
          if (!data.ok) return finalize(false);
          const raw = String(data.raw || '');
          if (!raw.trim()) return finalize(false);
          let currentNow = '';
          try { currentNow = String(localStorage.getItem('gallery_blacklist') || ''); } catch { currentNow = ''; }
          if (currentNow.trim()) return finalize(false);
          try { localStorage.setItem('gallery_blacklist', raw); } catch { }
          try { blacklistInput.value = raw; } catch { }
          try { state.blacklist = parseWords(raw).map((x) => x.toLowerCase()); } catch { }
          finalize(true);
        } catch {
          finalize(false);
        }
      };

      try { window.addEventListener('message', onMsg); } catch { }
      timer = setTimeout(() => finalize(false), 2500);
      try {
        iframe = document.createElement('iframe');
        iframe.style.position = 'fixed';
        iframe.style.left = '-9999px';
        iframe.style.top = '-9999px';
        iframe.style.width = '1px';
        iframe.style.height = '1px';
        iframe.style.opacity = '0';
        iframe.style.pointerEvents = 'none';
        const targetOrigin = encodeURIComponent(window.location.origin);
        iframe.src = `${oldOrigin}/api/migrate/blacklist?target_origin=${targetOrigin}`;
        document.body.appendChild(iframe);
      } catch {
        finalize(false);
      }
    });
    if (!okIframe) {
      try { ensureImportOldBlacklistButton(oldOrigin); } catch { }
    } else {
      try { if (importOldBlacklistBtn) { importOldBlacklistBtn.remove(); importOldBlacklistBtn = null; } } catch { }
    }
    return okIframe;
  }

  window.BlacklistMigrate = {
    runInBackground() {
      if (!oldBlacklistMigrationEnabled()) return;
      try {
        migrateBlacklistFromOldDomainIfNeeded().then((ok) => {
          if (!ok) return;
          // 旧域名黑名单迁移成功后，只刷新前端过滤结果，不阻塞初始作品加载。
          try { refreshCurrentGallery({ preserveScroll: true }); } catch { }
        }).catch(() => { });
      } catch { }
    },
  };
})();
