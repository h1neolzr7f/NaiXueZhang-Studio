(function () {
  function host() {
    let el = document.getElementById("uiToastHost");
    if (!el) {
      el = document.createElement("div");
      el.id = "uiToastHost";
      el.className = "ui-toast-host";
      document.body.appendChild(el);
    }
    return el;
  }

  function toast(message, kind, ms) {
    const text = String(message || "").trim();
    if (!text) return;
    const el = document.createElement("div");
    el.className = "ui-toast" + (kind ? " " + kind : "");
    el.textContent = text;
    host().appendChild(el);
    const ttl = Math.max(1400, Number(ms) || 2800);
    window.setTimeout(() => {
      el.classList.add("out");
      window.setTimeout(() => el.remove(), 220);
    }, ttl);
  }

  window.UiToast = { show: toast, ok: (m, ms) => toast(m, "ok", ms), err: (m, ms) => toast(m, "err", ms) };
})();
