(function () {
  const DEFAULT_TIMEOUT_MS = 15000;

  function withTimeout(signal, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || DEFAULT_TIMEOUT_MS);
    if (signal) {
      if (signal.aborted) controller.abort();
      else signal.addEventListener("abort", () => controller.abort(), { once: true });
    }
    return { signal: controller.signal, clear: () => clearTimeout(timer) };
  }

  let sessionTokenPromise = null;
  function getSessionToken() {
    if (!sessionTokenPromise) {
      sessionTokenPromise = fetch("/api/session-token", { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : {}))
        .then((data) => (data && data.token) || "")
        .catch(() => "");
    }
    return sessionTokenPromise;
  }

  async function raw(path, options) {
    const opts = Object.assign({}, options || {});
    const method = (opts.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
      const token = await getSessionToken();
      if (token) {
        opts.headers = Object.assign({}, opts.headers || {}, { "X-Session-Token": token });
      }
    }
    const timeout = withTimeout(opts.signal, opts.timeoutMs);
    delete opts.timeoutMs;
    opts.signal = timeout.signal;
    try {
      // Keep the native Response contract for callers that need status/headers/body parsing.
      return await fetch(path, opts);
    } finally {
      timeout.clear();
    }
  }

  async function request(path, options) {
    const opts = options || {};
    const init = {
      method: opts.method || "GET",
      headers: Object.assign({}, opts.headers || {}),
      cache: opts.cache || "no-store",
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = init.headers["Content-Type"] || "application/json";
      init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
    }
    const res = await raw(path, init);
    const contentType = res.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await res.json()
      : await res.text();
    if (!res.ok) {
      const rawDetail = payload && payload.detail;
      let message = `${path} ${res.status}`;
      if (typeof rawDetail === "string" && rawDetail.trim()) {
        message = rawDetail;
      } else if (Array.isArray(rawDetail)) {
        message = rawDetail
          .map((item) => (typeof item === "string" ? item : (item && item.msg) || JSON.stringify(item)))
          .filter(Boolean)
          .join("；") || message;
      } else if (rawDetail && typeof rawDetail === "object") {
        message = rawDetail.message || rawDetail.msg || JSON.stringify(rawDetail);
      } else if (payload && typeof payload === "string" && payload.trim()) {
        message = payload;
      }
      const err = new Error(message);
      err.status = res.status;
      err.payload = payload;
      err.detail = rawDetail;
      throw err;
    }
    return payload;
  }

  function get(path, options) {
    return request(path, Object.assign({}, options || {}, { method: "GET" }));
  }

  function post(path, body, options) {
    return request(path, Object.assign({}, options || {}, { method: "POST", body }));
  }

  // Legacy plugin compatibility: older upgraded modules call `fetchJson(path, init)`
  // with the native fetch-style method/body shape. Keep that contract while the
  // rest of the application uses request/get/post.
  function fetchJson(path, options) {
    return request(path, options || {});
  }

  window.ApiClient = {
    raw,
    request,
    get,
    post,
    fetchJson,
  };
})();
