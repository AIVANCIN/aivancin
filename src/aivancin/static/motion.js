/* Apply the device or explicit preference before first paint. */
(function () {
  "use strict";
  let mode = "auto";
  const root = document.documentElement;
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  const valid = value => ["auto", "force", "reduce"].includes(value);
  try {
    const saved = localStorage.getItem("aivancin-motion");
    if (valid(saved)) mode = saved;
  } catch (e) { /* The setting remains usable without storage. */ }
  const requested = new URLSearchParams(window.location.search).get("motion");
  if (valid(requested)) {
    mode = requested;
    try { localStorage.setItem("aivancin-motion", mode); } catch (e) {}
  }
  function apply() {
    root.dataset.motion = mode === "force" || (mode === "auto" && !query.matches) ? "on" : "off";
    root.dataset.motionMode = mode;
    root.dataset.motionPaused = document.hidden ? "true" : "false";
  }
  apply();
  query.addEventListener("change", apply);
  document.addEventListener("visibilitychange", apply);
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-motion-setting]").forEach(setting => {
      setting.hidden = false;
      setting.querySelectorAll("input").forEach(input => {
        input.checked = input.value === mode;
        input.addEventListener("change", () => {
          if (!input.checked || !valid(input.value)) return;
          mode = input.value;
          try { localStorage.setItem("aivancin-motion", mode); } catch (e) {}
          apply();
        });
      });
    });
  });
})();
