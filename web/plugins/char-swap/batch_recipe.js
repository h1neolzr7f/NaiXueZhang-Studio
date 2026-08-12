import { state } from "./state.js?v=f80b97d795";

function defaultTargetForMode(mode) {
  if (mode === "replace_female") return "auto_female";
  if (mode === "replace_male") return "auto_male";
  return "0";
}

function alignedTargetForMode(mode, target) {
  const value = String(target || "");
  const autoTargets = new Set(["", "auto_creature", "auto_female", "auto_male"]);
  if (autoTargets.has(value)) return defaultTargetForMode(mode);
  if (mode === "replace_female" && value === "all_male") return "all_female";
  if (mode === "replace_male" && value === "all_female") return "all_male";
  return value;
}

export function syncBatchTargetSlot() {
  const slotEl = document.getElementById("batchSlot");
  const modeEl = document.getElementById("batchMode");
  const creatureEl = document.getElementById("batchReplaceCreature");
  if (!slotEl || !modeEl) return;
  const mode = modeEl.value || "replace";
  if (creatureEl && creatureEl.checked) {
    slotEl.value = "auto_creature";
    return;
  }
  slotEl.value = alignedTargetForMode(mode, slotEl.value);
}

export function buildRecipeFromForm() {
  const charOn = document.getElementById("batchCharEnabled");
  const styleOn = document.getElementById("batchStyleEnabled");
  const presetEl = document.getElementById("batchPreset");
  const slotEl = document.getElementById("batchSlot");
  const modeEl = document.getElementById("batchMode");
  const findEl = document.getElementById("batchStyleFind");
  const replEl = document.getElementById("batchStyleReplace");
  const sanEl = document.getElementById("batchSanitize");
  const replaceCreatureEl = document.getElementById("batchReplaceCreature");
  const selectedMode = modeEl ? modeEl.value : "replace";
  const replaceCreature = replaceCreatureEl ? replaceCreatureEl.checked : false;
  const selectedTarget = slotEl ? slotEl.value : "";
  const targetCharIndex = replaceCreature
    ? (selectedTarget || "auto_creature")
    : alignedTargetForMode(selectedMode, selectedTarget);
  return {
    auto_sanitize: sanEl ? sanEl.checked : true,
    prompt_profile: (state.pluginConfig && state.pluginConfig.prompt_profile) || "native",
    preserve_action: false,
    preserve_center: true,
    transform: {
      enabled: charOn ? charOn.checked : false,
      mode: replaceCreature ? "creature_to_partner" : selectedMode,
      gender: String(selectedMode || "").includes("female") ? "female" : "male",
      preset_id: presetEl ? presetEl.value : "",
      target_char_index: targetCharIndex,
      replace_creature: replaceCreature,
    },
    style: {
      find: styleOn && styleOn.checked && findEl ? findEl.value.trim() : "",
      replace: replEl ? replEl.value : "",
    },
    replace_creature: replaceCreature,
    sanitize: {
      enabled: sanEl ? sanEl.checked : true,
      filter_racial: true,
      filter_gore: true,
      filter_creature: false,
    },
  };
}
