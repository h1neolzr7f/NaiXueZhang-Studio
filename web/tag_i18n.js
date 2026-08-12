const TagI18n = (() => {
  const map = Object.create(null);
  let ready = false;
  const ARK_SUFFIX = /^(.+?)\(アークナイツ\)$/;

  const CACHE_KEY = 'aitag_tag_dict_v1';

  function humanizeDanbooru(tag) {
    const raw = String(tag || '').trim();
    if (!raw) return '';
    let base = raw.replace(/_\(arknights\)$/i, '').replace(/\(アークナイツ\)$/i, '');
    base = base.replace(/_/g, ' ').trim();
    if (!base) return raw;
    return base.split(/\s+/).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
  }

  async function load() {
    try {
      const cached = sessionStorage.getItem(CACHE_KEY);
      if (cached) {
        const data = JSON.parse(cached);
        if (data && typeof data === 'object') {
          Object.keys(map).forEach((key) => delete map[key]);
          Object.assign(map, data);
          ready = true;
        }
      }
      const data = await ApiClient.get('/api/tags/dict');
      Object.keys(map).forEach((key) => delete map[key]);
      Object.assign(map, data || {});
      ready = true;
      try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(data || {})); } catch { }
      return true;
    } catch {
      return ready;
    }
  }

  function translate(tag) {
    const original = String(tag || '').trim();
    if (!original) {
      return { original: '', zh: '', translated: false, source: 'empty' };
    }
    if (map[original]) {
      const zh = map[original];
      return {
        original,
        zh,
        translated: zh !== original,
        source: 'dict',
      };
    }
    const m = ARK_SUFFIX.exec(original);
    if (m && map[m[1]]) {
      return {
        original,
        zh: map[m[1]],
        translated: true,
        source: 'arknights_suffix',
      };
    }
    if (/[\u4e00-\u9fff]/.test(original) && !/[\u3040-\u30ff]/.test(original)) {
      return {
        original,
        zh: original,
        translated: false,
        source: 'already_zh',
      };
    }
    if (/_/.test(original) && !/[\u3040-\u30ff]/.test(original)) {
      const zh = humanizeDanbooru(original);
      if (zh && zh.toLowerCase() !== original.toLowerCase()) {
        return {
          original,
          zh,
          translated: true,
          source: 'danbooru_humanize',
          danbooru: true,
        };
      }
    }
    return {
      original,
      zh: original,
      translated: false,
      source: 'fallback',
    };
  }

  return {
    load,
    translate,
    get ready() {
      return ready;
    },
    get size() {
      return Object.keys(map).length;
    },
  };
})();