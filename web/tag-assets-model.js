// Pure normalization for the AITag asset workbench. This module never calls
// network or generation APIs; the page controller owns those side effects.

export const DRAFT_KEY = "aitag.studio.draft.v1";

export function workFrom(item) {
  return item?.work && typeof item.work === "object" ? item.work : (item || {});
}

export function workIdFrom(item) {
  const work = workFrom(item);
  return String(work.work_id || work.id || item?.work_id || item?.id || "").trim();
}

export function titleFrom(item) {
  const work = workFrom(item);
  return String(work.title || work.name || work.label || workIdFrom(item) || "未命名资产").trim();
}

export function tagsFrom(item) {
  const work = workFrom(item);
  const value = work.tags ?? item?.tags ?? [];
  if (Array.isArray(value)) return value.map((tag) => String(tag || "").trim()).filter(Boolean);
  if (typeof value !== "string") return [];
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed.map((tag) => String(tag || "").trim()).filter(Boolean);
  } catch (_) { /* use the forgiving parser below */ }
  return value.replace(/^\[|\]$/g, "").split(/[,，]/).map((tag) => tag.replace(/["']/g, "").trim()).filter(Boolean);
}

export function aiTypeFrom(item) {
  const work = workFrom(item);
  return String(work.ai_type || work.AI_type || item?.ai_type || "").trim();
}

export function isNai(item) {
  const type = aiTypeFrom(item).toLowerCase();
  return !type || type.includes("nai") || type.includes("novelai");
}

export function isAdult(item) {
  return tagsFrom(item).some((tag) => {
    const normalized = tag.toLowerCase().replace(/[_\s]+/g, "-");
    return normalized === "r-18" || normalized === "r-18g" || normalized === "nsfw"
      || normalized === "explicit" || normalized.includes("rating:explicit");
  });
}

function imageUrl(image) {
  if (typeof image === "string") return image;
  return image?.url || image?.image_url || image?.original_url || image?.src || "";
}

function thumbUrl(image) {
  if (typeof image === "string") return image;
  return image?.thumbnail_url || image?.thumb_url || image?.preview_url || imageUrl(image);
}

export function imagesFrom(item) {
  const work = workFrom(item);
  const values = work.images || item?.images || work.image_urls || item?.image_urls || [];
  const list = Array.isArray(values) ? values : [];
  const normalized = list.map((image, position) => ({
    imageIndex: Number.isInteger(Number(image?.image_index)) ? Number(image.image_index) : position,
    url: imageUrl(image),
    thumbUrl: thumbUrl(image),
    width: Number(image?.width || 0),
    height: Number(image?.height || 0),
  })).filter((image) => image.url || image.thumbUrl);
  if (normalized.length) return normalized;
  const fallback = work.thumbnail_url || work.thumb_url || item?.thumb_url || item?.image_url || item?.preview_url || "";
  return fallback ? [{ imageIndex: 0, url: fallback, thumbUrl: fallback, width: 0, height: 0 }] : [];
}

export function qualificationFrom(item) {
  const work = workFrom(item);
  const raw = work.qualification ?? item?.qualification;
  const reasons = work.qualification_reasons || item?.qualification_reasons || [];
  const kind = typeof raw === "string" ? raw.trim().toLowerCase() : raw;
  const qualified = raw === undefined || raw === null
    ? null
    : (raw === true || ["direct", "remix-only", "qualified", "eligible", "ok"].includes(kind));
  const label = kind === "direct"
    ? "可直接建立 NAI 草稿"
    : (kind === "remix-only" ? "仅复用兼容配方" : (qualified === false ? "需要人工确认" : (qualified === true ? "可建立草稿" : "资格待详情确认")));
  return {
    qualified,
    kind: typeof kind === "string" ? kind : "",
    label,
    reasons: Array.isArray(reasons) ? reasons.map(String) : [String(reasons || "")].filter(Boolean),
  };
}

export function visibleOnlineItems(items, { naiOnly = true, safeOnly = true } = {}) {
  // Display enhancement only. Backend nai_only/safe_only remains authoritative.
  return (items || []).filter((item) => (!naiOnly || isNai(item)) && (!safeOnly || !isAdult(item)));
}

export function externalHref(item) {
  const workId = workIdFrom(item);
  const candidate = String(workFrom(item).external_url || item?.source?.external_url || "").trim();
  if (/^https:\/\/aitag\.win\//i.test(candidate)) return candidate;
  return `https://aitag.win/i/${encodeURIComponent(workId)}`;
}

export function promptTextFrom(item) {
  const work = workFrom(item);
  const candidates = [
    work.prompt,
    work.base_caption,
    work.comment?.prompt,
    work.comment?.v4_prompt?.caption?.base_caption,
    item?.recipe?.texts?.prompt,
    item?.recipe?.texts?.base_caption,
  ];
  return String(candidates.find((value) => String(value || "").trim()) || "").trim();
}

export function characterCandidatesFrom(item) {
  const work = workFrom(item);
  const values = item?.character_candidates || work.character_candidates || [];
  if (!Array.isArray(values)) return [];
  return values.map((candidate, position) => ({
    candidateId: String(candidate?.candidate_id || "").trim(),
    imageIndex: Number.isInteger(Number(candidate?.image_index)) ? Number(candidate.image_index) : 0,
    slotIndex: Number.isInteger(Number(candidate?.slot_index)) ? Number(candidate.slot_index) : position,
    label: String(candidate?.label || candidate?.asset?.label || `角色槽 ${position + 1}`).trim(),
    caption: String(candidate?.caption || "").trim(),
    role: String(candidate?.role || "").trim(),
  })).filter((candidate) => candidate.candidateId && candidate.slotIndex >= 0 && candidate.slotIndex <= 5);
}

export function licenseFrom(item) {
  const work = workFrom(item);
  const metadata = work.metadata && typeof work.metadata === "object" ? work.metadata : {};
  const provenance = item?.provenance && typeof item.provenance === "object" ? item.provenance : {};
  const name = String(
    item?.license_name || work.license_name || metadata.license_name || metadata.license
      || provenance.source_license || "unknown"
  ).trim() || "unknown";
  const status = String(item?.license_status || work.license_status || provenance.license_status || (name === "unknown" ? "unknown" : "source-provided")).trim();
  const sourceUrl = String(item?.source_url || work.external_url || provenance.source_url || externalHref(item)).trim();
  return { name, status, sourceUrl };
}

export function normalizeDetail(payload, fallbackItem) {
  const root = payload && typeof payload === "object" ? payload : {};
  const work = root.work && typeof root.work === "object" ? root.work : workFrom(fallbackItem);
  const merged = { ...root, work, images: root.images || work.images || imagesFrom(fallbackItem) };
  return {
    payload: merged,
    work,
    workId: workIdFrom(merged),
    title: titleFrom(merged),
    tags: tagsFrom(merged),
    images: imagesFrom(merged),
    characterCandidates: characterCandidatesFrom(merged),
    source: root.source || work.source || {},
    license: licenseFrom(merged),
    qualification: qualificationFrom(merged),
    generationCalls: Number(root.generation_calls || 0),
  };
}

export function normalizeDraftResponse(result) {
  if (!result || result.ok === false || !result.draft || typeof result.draft !== "object") {
    throw new Error(result?.message || "在线服务没有返回可用 Studio 草稿");
  }
  const generationCalls = Number(result.generation_calls);
  if (!Number.isFinite(generationCalls) || generationCalls !== 0) {
    throw new Error("安全检查失败：在线草稿响应未证明 generation_calls: 0");
  }
  const persisted = result.persisted !== false;
  const draftId = String(result.draft_id || "").trim();
  if (persisted && !/^[0-9a-f]{16}$/i.test(draftId)) {
    throw new Error("安全检查失败：在线草稿响应缺少可恢复的 draft_id");
  }
  const studioUrl = String(result.studio_url || "").trim()
    || (persisted ? `/studio?aitag=1&remix=1&draft=${encodeURIComponent(draftId)}` : "/studio?aitag=1&remix=1");
  if (!studioUrl.startsWith("/studio")) {
    throw new Error("安全检查失败：studio_url 不是本地 Studio 路径");
  }
  if (persisted && !studioUrl.includes(`draft=${draftId}`) && !studioUrl.includes(`draft=${encodeURIComponent(draftId)}`)) {
    throw new Error("安全检查失败：studio_url 未携带服务端 draft_id");
  }
  return {
    draft: { ...result.draft, draftId, ts: Date.now() },
    draftId,
    recipe: result.recipe || null,
    studioUrl,
    generationCalls,
    persisted,
    persistenceWarning: String(result.persistence_warning || "").trim(),
  };
}

export function localReferenceId(item) {
  return String(item?.reference_id || item?.id || "").trim();
}

export function localReferenceLabel(item) {
  return String(item?.label || item?.source_id || localReferenceId(item) || "未命名本地角色").trim();
}
