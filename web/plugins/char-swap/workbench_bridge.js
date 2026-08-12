// Stable Interface between preset/reference Modules and the Studio workbench.
// Registration keeps the production Modules independent from panel.js and avoids
// a presets <-> panel import cycle.

const workbenchHandlers = {};

export function setWorkbenchHandlers(handlers = {}) {
  Object.assign(workbenchHandlers, handlers || {});
}

export function callWorkbench(name, ...args) {
  const fn = workbenchHandlers[name];
  if (typeof fn !== "function") {
    throw new Error(`CharSwap workbench handler is not registered: ${name}`);
  }
  return fn(...args);
}

export function syncStyleFromResponse(...args) { return callWorkbench("syncStyleFromResponse", ...args); }
export function updateDraftPreview(...args) { return callWorkbench("updateDraftPreview", ...args); }
export function renderStyleRows(...args) { return callWorkbench("renderStyleRows", ...args); }
export function renderSlotRows(...args) { return callWorkbench("renderSlotRows", ...args); }
export function loadExtract(...args) { return callWorkbench("loadExtract", ...args); }
export function resetDraftFromOriginal(...args) { return callWorkbench("resetDraftFromOriginal", ...args); }
export function syncSeedUi(...args) { return callWorkbench("syncSeedUi", ...args); }
export function runTransform(...args) { return callWorkbench("runTransform", ...args); }
export function runTransformAllPages(...args) { return callWorkbench("runTransformAllPages", ...args); }
export function runTransformMulti(...args) { return callWorkbench("runTransformMulti", ...args); }
export function runTransformMultiAllPages(...args) { return callWorkbench("runTransformMultiAllPages", ...args); }
