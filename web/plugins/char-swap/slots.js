export function slotGender(ch) {
  const g = (ch && ch.gender) || (ch && ch.bundle && ch.bundle.gender);
  if (g === "male" || g === "female") return g;
  const tags = ((ch && ch.identity_tags) || []).map((t) => String(t).toLowerCase());
  if (tags.some((t) => t.startsWith("cure") || t === "precure")) return "female";
  return "unknown";
}

export function countGenderSlots(chars, gender) {
  return (chars || []).filter((ch) => slotGender(ch) === gender).length;
}

export function genderSlots(chars, gender) {
  return (chars || []).filter((ch) => slotGender(ch) === gender);
}

export function targetIndexForGenderSwap(chars, ch, gender) {
  const slots = genderSlots(chars || [], gender);
  if (slots.length === 1) return slots[0].index;
  if (ch && slotGender(ch) === gender) return ch.index;
  // 图上没有该性别槽时（如怪物/未知槽），直接替换用户点击的当前行，
  // 而不是落到 auto_male/auto_female（后者会被 gender_slot_index 优先解析而报错）。
  if (ch && ch.index !== undefined && ch.index !== null && ch.index !== "") {
    return ch.index;
  }
  return gender === "male" ? "auto_male" : "auto_female";
}

export function genderSlotIndex(chars, ch, gender) {
  const slots = genderSlots(chars || [], gender);
  if (!slots.length) return "";
  if (!ch) return 0;
  const pos = slots.findIndex((s) => Number(s.index) === Number(ch.index));
  return pos >= 0 ? pos : 0;
}

export function slotIdentityKeys(ch) {
  const noise = new Set([
    "",
    "1boy", "1girl", "2boys", "2girls", "3boys", "3girls",
    "male_focus", "female_focus", "boy", "girl", "boys", "girls",
    "unknown", "unknown_character", "未知", "未知角色", "未知男角色", "未知女角色",
    "女槽", "男槽",
  ]);
  const keys = [];
  const add = (raw) => {
    const text = String(raw || "").trim();
    if (!text) return;
    const key = text.toLowerCase()
      .replace(/^\d+(?:\.\d+)?::/, "")
      .replace(/::$/, "")
      .replace(/^[{}[\]()\s]+|[{}[\]()\s]+$/g, "")
      .replace(/\s+/g, "_");
    if (!key || noise.has(key) || key.startsWith("未知")) return;
    if (!keys.includes(key)) keys.push(key);
  };
  add(ch && ch.ark_library_tag);
  add(ch && ch.oc_label);
  add(ch && ch.summary);
  add(ch && ch.display_name);
  (ch && ch.identity_tags || []).forEach(add);
  const bundle = ch && ch.bundle && typeof ch.bundle === "object" ? ch.bundle : {};
  (bundle.identity || []).forEach(add);
  return keys;
}

export function applyGenderSwapTarget(body, chars, ch, gender, scope) {
  if (scope === "all_slots") {
    body.target_char_index = gender === "male" ? "all_male" : "all_female";
    delete body.gender_slot_index;
    return;
  }
  if (scope === "all") {
    body.target_char_index = gender === "male" ? "all_male" : "all_female";
    delete body.gender_slot_index;
    body.match_identity_keys = slotIdentityKeys(ch);
    body.require_match_identity = true;
    body.skip_missing_slots = true;
    return;
  }
  body.target_char_index = targetIndexForGenderSwap(chars || [], ch, gender);
  body.gender_slot_index = genderSlotIndex(chars || [], ch, gender);
}

export function slotOcPreview(ch) {
  if (!ch) return "";
  if (ch.oc_preview) return String(ch.oc_preview);
  if (!ch.is_oc) return "";
  const cap = String(ch.char_caption || "").trim();
  if (!cap) return "";
  const stripGender = (text) => text
    .replace(/^1girl,\s*female_focus,\s*/i, "")
    .replace(/^1boy,\s*male_focus,\s*/i, "");
  return stripGender(cap);
}

export function slotDisplayName(ch) {
  // Prefer raw caption for display when identity inference is empty/noisy (restore-original debt).
  const oc = slotOcPreview(ch);
  if (oc) {
    const name = String(ch.summary || "").trim();
    return name ? `${name} · ${oc.slice(0, 64)}${oc.length > 64 ? "…" : ""}` : oc.slice(0, 80);
  }
  if (ch.identity_tags && ch.identity_tags.length) {
    const joined = ch.identity_tags.join(", ");
    // Avoid action-only noise masquerading as identity
    if (!/^(boy|girl|1boy|1girl|disembodied|hand|solo)(,\s*)*$/i.test(joined.trim())) {
      return joined;
    }
  }
  if (ch.summary && String(ch.summary).trim() && !/^未知/.test(String(ch.summary))) {
    return ch.summary;
  }
  const cap = String(ch.char_caption || "").trim();
  if (cap) {
    const short = cap.split(",").slice(0, 4).map((s) => s.trim()).filter(Boolean).join(", ");
    return short || cap.slice(0, 80);
  }
  return ch.summary || "未识别角色";
}

export function genderRoleLabel(gender, count) {
  const n = Math.max(0, Number(count) || 0);
  const side = gender === "male" ? "男" : "女";
  if (n <= 1) return `${side}角`;
  if (n === 2) return `双${side}主`;
  return `多${side}主(${n})`;
}
