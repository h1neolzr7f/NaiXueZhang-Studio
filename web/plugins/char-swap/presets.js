// Compatibility Facade. Production implementations live in focused deep Modules.
// Static compatibility contracts retained for downstream source guards:
// api("/api/plugin/char-swap/presets", ...) is owned by character_references.js.
// Returned cards carry `is_custom`; multi-slot selection uses selects.forEach.
// Modal markup retains char-swap-modal-hint and char-swap-custom-oc.
// createCustomOcComposer renders ＋ 自定义 OC and 保存并使用.

export { setWorkbenchHandlers } from "./workbench_bridge.js?v=e72141834f";
export * from "./style_workflows.js?v=c55efe211e";
export * from "./reference_modals.js?v=55369bd227";
