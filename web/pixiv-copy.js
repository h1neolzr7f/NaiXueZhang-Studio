// 由 pixiv.js 拆分：文案生成与账号表单。经典脚本，依赖 pixiv.js 中的全局函数，加载顺序须先于 pixiv.js。

// 文案表单脏标记：用户手改过任一字段后置脏，自动生成前需确认，避免静默覆盖
let postFormDirty = false;

function markPostFormDirty() {
  postFormDirty = true;
}

function resetPostFormDirty() {
  postFormDirty = false;
}

// 日/中分栏 → 上传用合并字段的实时同步：改上面四格时合并字段跟着走，
// 避免「改了标题（日）但投稿仍用旧合并标题」。
function syncMergedPostFields(sourceId) {
  const ja = document.getElementById("postTitleJa").value.trim();
  const zh = document.getElementById("postTitleZh").value.trim();
  const capJa = document.getElementById("postCaptionJa").value.trim();
  const capZh = document.getElementById("postCaptionZh").value.trim();
  if (sourceId === "postTitleJa" || sourceId === "postTitleZh") {
    document.getElementById("postTitle").value = ja || zh;
  } else if (sourceId === "postCaptionJa" || sourceId === "postCaptionZh") {
    document.getElementById("postCaption").value = capJa || capZh;
  }
}

["postTitleJa", "postTitleZh", "postCaptionJa", "postCaptionZh", "postTitle", "postCaption", "postTags"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("input", () => {
    markPostFormDirty();
    syncMergedPostFields(id);
  });
});

function getCopyPrimaryImageId() {
  const groups = getSelectedGroups();
  if (groups.length > 1) {
    for (const g of groups) {
      const ids = g.image_ids || [];
      if (ids.length) return ids[0];
    }
  }
  return selectedId;
}

async function generateCopy(silent) {
  const copyId = getCopyPrimaryImageId();
  if (!copyId) {
    if (!silent) alert("请先选一张图或系列");
    return;
  }
  // 表单已被手改过时，自动生成前必须确认，避免静默覆盖用户文案
  if (postFormDirty) {
    const ok = window.confirm("当前文案已被你手动修改过，重新生成会覆盖这些修改。确定继续吗？");
    if (!ok) return;
  }
  await saveUploadFlags();
  const st = document.getElementById("copyStatus");
  setStatus(st, "正在生成标题与简介…");
  const btn = document.getElementById("genPost");
  btn.disabled = true;
  try {
    const res = await window.ApiClient.raw("/api/pixiv/director/post", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_id: copyId,
        extra: [
          document.getElementById("postExtra").value.trim(),
          getSelectedGroups().length > 1
            ? `共 ${getMergedSelectionStats().total} 页，来自 ${getSelectedGroups().length} 个系列合并为一篇投稿。`
            : "",
        ].filter(Boolean).join(" "),
        persona: personaCache,
        save_draft: true,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "生成失败");
    fillPostForm(data.post || {});
    resetPostFormDirty();
    const src = (data.post && data.post.source) || "";
    const tagSrc = data.tag_source === "generation_snapshot" ? "生成图 tag" : "源站 tag";
    const piped = (data.pipeline_ran || []).length ? " · 已补跑流水线" : "";
    setStatus(st, `已生成（${src || "ok"} · ${tagSrc}${piped}）`, "ok");
    if (data.post && data.post.warning && !silent) alert(data.post.warning);
  } catch (e) {
    setStatus(st, e.message, "err");
    if (!silent) alert(e.message);
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("aiProvider").addEventListener("change", (e) => {
  const p = PRESETS[e.target.value] || {};
  if (p.base) document.getElementById("aiBase").value = p.base;
  if (p.model) document.getElementById("aiModel").value = p.model;
});

document.getElementById("addAccount").addEventListener("click", async () => {
  const token = document.getElementById("pxRefresh").value.trim();
  if (!token) return alert("请填写 refresh_token，或改用上方「新建账号槽」后通行密钥登录");
  const btn = document.getElementById("addAccount");
  btn.disabled = true;
  try {
    const res = await window.ApiClient.raw("/api/pixiv/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        refresh_token: token,
        label: document.getElementById("accLabel").value.trim(),
        direction: document.getElementById("direction").value.trim(),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || "添加失败");
    document.getElementById("pxRefresh").value = "";
    document.getElementById("accLabel").value = "";
    await loadConfig();
    if (data.auth) {
      renderPixivAuth({
        ok: data.auth.ok,
        has_refresh_token: true,
        user: data.auth.user,
        message: data.auth.message,
        error: data.auth.error,
      });
      renderAccounts();
    }
    if (data.auth && !data.auth.ok) {
      setStatus(document.getElementById("pixivAuthStatus"), (data.auth.error && data.auth.error.hint) || data.auth.message || "登录检测失败", "err");
      toast("账号已添加但登录检测失败", "err");
    } else if (data.auth && data.auth.ok) {
      setStatus(document.getElementById("pixivAuthStatus"), "账号已添加并登录成功", "ok");
      toast("账号已添加并登录成功", "ok");
    }
    refreshReadyStrip();
  } catch (e) {
    renderPixivAuth({ ok: false, has_refresh_token: true, message: e.message, error: { hint: e.message } });
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("importAccountsBtn")?.addEventListener("click", async () => {
  const text = (document.getElementById("pxImportBatch")?.value || "").trim();
  if (!text) return alert("请粘贴要导入的账号（每行一个）");
  const btn = document.getElementById("importAccountsBtn");
  const st = document.getElementById("pxImportStatus");
  const box = document.getElementById("pxImportResults");
  btn.disabled = true;
  setStatus(st, "正在批量导入…");
  if (box) box.innerHTML = "";
  try {
    const res = await window.ApiClient.raw("/api/pixiv/accounts/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        verify: !!document.getElementById("pxImportVerify")?.checked,
        skip_duplicates: !!document.getElementById("pxImportSkipDup")?.checked,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || "导入失败");
    setStatus(st, data.message || "导入完成", data.ok ? "ok" : "err");
    toast(data.message || "导入完成", data.ok ? "ok" : "err");
    if (box && Array.isArray(data.results)) {
      box.innerHTML = data.results.map((r) => {
        const cls = r.skipped ? "skip" : (r.ok ? "ok" : "err");
        const tag = r.skipped ? "跳过" : (r.ok ? "成功" : "失败");
        const who = r.user_name ? ` · ${escapeHtml(r.user_name)}` : "";
        return `<div class="px-import-row ${cls}">L${escapeHtml(r.line || "?")} · ${tag} · ${escapeHtml(r.label || "")}${who}<br>${escapeHtml(r.message || "")}</div>`;
      }).join("");
    }
    await loadConfig();
    refreshReadyStrip();
    if (data.ok_count > 0) document.getElementById("pxImportBatch").value = "";
  } catch (e) {
    setStatus(st, e.message, "err");
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("createAccountSlot")?.addEventListener("click", async () => {
  const btn = document.getElementById("createAccountSlot");
  btn.disabled = true;
  try {
    const label = (document.getElementById("accLabel")?.value || "").trim()
      || prompt("给新号起个备注名（可空）", `新号${accounts.length + 1}`)
      || "";
    const res = await window.ApiClient.raw("/api/pixiv/accounts/slot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label,
        direction: (document.getElementById("direction")?.value || "").trim(),
        set_active: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || "创建失败");
    await loadConfig();
    setAuthTab("passkey");
    setStatus(
      document.getElementById("pixivAuthStatus"),
      data.message || "账号槽已创建，请点「通行密钥登录」完成注册配置",
      "ok",
    );
    toast("账号槽已创建，请通行密钥登录绑定 Pixiv", "ok");
    refreshReadyStrip();
  } catch (e) {
    setStatus(document.getElementById("pixivAuthStatus"), e.message, "err");
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("savePixivToken").addEventListener("click", async () => {
  const token = document.getElementById("pxRefresh").value.trim();
  if (!token) return alert("请填写 refresh_token");
  if (!activeAccountId) return alert("请先添加或选择一个账号");
  const btn = document.getElementById("savePixivToken");
  btn.disabled = true;
  try {
    const res = await window.ApiClient.raw(`/api/pixiv/accounts/${encodeURIComponent(activeAccountId)}/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || "保存失败");
    document.getElementById("pxRefresh").value = "";
    await loadConfig();
    if (data.auth) {
      renderPixivAuth({
        ok: data.auth.ok,
        has_refresh_token: true,
        user: data.auth.user,
        message: data.auth.message,
        error: data.auth.error,
      });
      renderAccounts();
    }
    if (data.auth && !data.auth.ok) {
      setStatus(document.getElementById("pixivAuthStatus"), (data.auth.error && data.auth.error.hint) || data.auth.message || "登录失败", "err");
    } else if (data.auth && data.auth.ok) {
      setStatus(document.getElementById("pixivAuthStatus"), "Token 已更新，登录有效", "ok");
    }
  } catch (e) {
    renderPixivAuth({ ok: false, has_refresh_token: true, message: e.message, error: { hint: e.message } });
  } finally {
    btn.disabled = false;
  }
});

