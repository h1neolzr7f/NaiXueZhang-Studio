(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const message = byId("message");
  const buttons = Array.from(document.querySelectorAll("button"));
  const labels = {
    total: "扫描总数",
    rendered: "成功生成",
    created: "新建文件",
    failed: "失败数量",
    works: "索引作品",
    orphan_files: "孤儿文件",
    orphan_bytes: "孤儿体积",
    deleted_files: "已删文件",
    deleted_bytes: "释放空间",
    referenced_files: "有效引用",
    filename: "快照文件",
    asset_count: "快照资产",
    asset_bytes: "资产体积",
    database_bytes: "数据库体积",
    manifest_sha256: "清单校验",
    permanent_skip_works: "永久跳过作品",
    permanent_skip_pages: "永久跳过页数",
    sample_work_ids: "样例 work_id",
    reasons: "拒绝原因",
    staging_files: "staging 文件",
    staging_bytes: "staging 体积",
    candidates: "待迁移候选",
    migrated: "已迁移",
    skipped: "已跳过",
    bytes_before: "迁移前体积",
    bytes_after: "迁移后体积",
    bytes_saved: "预计节省",
    dry_run: "仅预估",
  };

  function formatBytes(value) {
    let size = Number(value || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
  }

  function formatReceiptValue(key, value) {
    if (key.endsWith("_bytes")) return formatBytes(value);
    if (typeof value === "boolean") return value ? "是" : "否";
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) return `${value.length} 项`;
    if (typeof value === "object") return `${Object.keys(value).length} 组`;
    return String(value);
  }

  function renderReceipt(receipt, heading = "操作完成") {
    const list = byId("receiptList");
    list.replaceChildren();
    const entries = Object.entries(receipt || {});
    if (!entries.length) entries.push(["status", "已完成，接口未返回明细"]);
    for (const [key, value] of entries) {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = labels[key] || key.replaceAll("_", " ");
      const result = document.createElement("strong");
      result.textContent = formatReceiptValue(key, value);
      item.append(name, result);
      list.append(item);
    }
    show(heading, false);
  }

  function busy(on) {
    buttons.forEach((button) => { button.disabled = on; });
  }

  function show(text, failed = false) {
    message.textContent = text;
    message.className = `action-message ${failed ? "fail" : "ok"}`;
  }

  function updateStorageMeter(item) {
    const assetBytes = Number(item.asset_bytes || 0);
    const quotaBytes = Number(item.quota_bytes || 0);
    const diskTotalBytes = Number(item.disk_total_bytes || 0);
    const diskUsedBytes = Number(item.disk_used_bytes || 0);
    const base = quotaBytes || Math.max(1, diskTotalBytes);
    const measuredBytes = quotaBytes ? assetBytes : diskUsedBytes;
    const percentage = Math.max(0, Math.min(100, (measuredBytes / base) * 100));
    const meter = byId("storageMeter");
    meter.style.width = `${percentage}%`;
    const wrapper = meter.parentElement;
    wrapper.setAttribute("aria-valuenow", String(Math.round(percentage)));
    wrapper.setAttribute(
      "aria-valuetext",
      `${quotaBytes ? "图库配额" : "磁盘空间"}已使用 ${formatBytes(measuredBytes)}，约 ${percentage.toFixed(1)}%`,
    );
  }

  async function loadStorage() {
    try {
      const data = await ApiClient.get("/api/maintenance/storage");
      const item = data.storage || {};
      byId("assetSize").textContent = formatBytes(item.asset_bytes);
      byId("storageDetail").textContent = [
        `${item.original_files || 0} 个原图`,
        `${item.thumbnail_files || 0} 个缩略图`,
        `数据库 ${formatBytes(item.database_bytes)}`,
        `磁盘剩余 ${formatBytes(item.disk_free_bytes)}`,
        item.quota_bytes ? `图库配额 ${formatBytes(item.quota_bytes)}` : "未设置图库上限",
      ].join(" · ");
      updateStorageMeter(item);
    } catch (error) {
      show(`读取存储状态失败：${error.message || error}`, true);
    }
  }

  async function run(endpoint, body, describe) {
    busy(true);
    show(`${describe}进行中…`);
    try {
      const data = await ApiClient.post(endpoint, body);
      renderReceipt(data.receipt || {}, `${describe}完成`);
      await loadStorage();
      return data;
    } catch (error) {
      const detail = error.message || String(error);
      renderReceipt({ status: "失败", error: detail }, `${describe}失败`);
      show(`${describe}失败：${detail}`, true);
      return null;
    } finally {
      busy(false);
    }
  }

  byId("refreshStorage").addEventListener("click", loadStorage);
  byId("rebuildThumbs").addEventListener("click", () => run("/api/maintenance/thumbnails/rebuild", {}, "缩略图重建"));
  byId("rebuildNaiTags").addEventListener("click", () => run("/api/maintenance/nai-tags/rebuild", {}, "NAI 分类重建"));
  byId("previewOrphans").addEventListener("click", () => run("/api/maintenance/orphans/preview", {}, "孤儿预览"));
  byId("cleanOrphans").addEventListener("click", async () => {
    const preview = await run("/api/maintenance/orphans/preview", {}, "孤儿预览");
    if (!preview) return;
    const count = Number(preview.receipt?.orphan_files || 0);
    if (!count) { show("没有需要清理的孤儿文件。"); return; }
    if (window.confirm(`确认永久删除 ${count} 个未引用文件？`)) {
      await run("/api/maintenance/orphans/clean", { confirm: true }, "孤儿清理");
    }
  });
  byId("createSnapshot").addEventListener("click", () => run("/api/maintenance/snapshot", {}, "图库快照"));

  async function loadPermanentSkips() {
    try {
      const data = await ApiClient.get("/api/maintenance/skips/permanent");
      const receipt = data.receipt || {};
      const summary = byId("permanentSkipSummary");
      if (summary) {
        const works = Number(receipt.permanent_skip_works || 0);
        const pages = Number(receipt.permanent_skip_pages || 0);
        const reasons = receipt.reasons && typeof receipt.reasons === "object"
          ? Object.entries(receipt.reasons).map(([k, v]) => `${k}:${v}`).join(" · ")
          : "无";
        summary.textContent = works
          ? `永久跳过 ${works} 个作品 / ${pages} 页。原因：${reasons || "—"}`
          : "当前没有永久跳过记录。";
      }
      renderReceipt(receipt, "永久跳过清单");
      return data;
    } catch (error) {
      show(`读取永久跳过失败：${error.message || error}`, true);
      return null;
    }
  }

  byId("refreshPermanentSkips")?.addEventListener("click", loadPermanentSkips);
  byId("previewStaging")?.addEventListener("click", () => run("/api/maintenance/staging/cleanup", {}, "staging 预览"));
  byId("cleanStaging")?.addEventListener("click", async () => {
    const preview = await run("/api/maintenance/staging/cleanup", {}, "staging 预览");
    if (!preview) return;
    const count = Number(preview.receipt?.staging_files || 0);
    if (!count) {
      show("staging 目录没有残留文件。");
      return;
    }
    if (window.confirm(`确认删除 ${count} 个 staging 残留文件？`)) {
      await run("/api/maintenance/staging/cleanup", { confirm: true }, "staging 清理");
    }
  });
  byId("previewWebpMigrate")?.addEventListener("click", () => run("/api/maintenance/originals/migrate-webp", {}, "WebP 迁移预估"));
  byId("runWebpMigrate")?.addEventListener("click", async () => {
    const preview = await run("/api/maintenance/originals/migrate-webp", {}, "WebP 迁移预估");
    if (!preview) return;
    const count = Number(preview.receipt?.candidates || preview.receipt?.migrated || 0);
    if (!count) {
      show("没有需要迁移的 PNG/JPEG 原图。");
      return;
    }
    const saved = formatBytes(preview.receipt?.bytes_saved || 0);
    if (window.confirm(`确认把约 ${count} 张旧原图压缩为 WebP？预估可节省 ${saved}。`)) {
      await run("/api/maintenance/originals/migrate-webp", { confirm: true }, "WebP 迁移");
    }
  });

  loadStorage();
  loadPermanentSkips();
})();
