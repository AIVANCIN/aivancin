/* Text size, bold text and contrast: applied before first paint from the saved choice, then kept in step with the
   Display & accessibility sheet. Saved per browser; without storage a choice lasts for the visit. Text size scales
   the root size, so every rem-sized text follows it, starting from the browser's own size. */
(function () {
  "use strict";
  const root = document.documentElement;
  const SIZES = [["Default", 100], ["Large", 112.5], ["Larger", 125], ["Extra large", 150], ["Largest", 175]];
  const KEY = { size: "aivancin-text-size", bold: "aivancin-bold-text", contrast: "aivancin-contrast" };
  const query = window.matchMedia("(prefers-contrast: more)");
  const read = key => { try { return localStorage.getItem(key); } catch (e) { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ } };

  let size = Number(read(KEY.size));
  if (!Number.isInteger(size) || size < 0 || size >= SIZES.length) size = 0;
  let bold = read(KEY.bold) === "on";
  // Contrast follows the device until the person chooses.
  let contrast = ["on", "off"].includes(read(KEY.contrast)) ? read(KEY.contrast) : "auto";
  const more = () => contrast === "on" || (contrast === "auto" && query.matches);

  function apply() {
    root.dataset.textSize = String(size);
    root.dataset.bold = bold ? "on" : "off";
    root.dataset.contrast = more() ? "more" : "standard";
  }
  apply();
  query.addEventListener("change", () => { apply(); sync(); });

  function sync() {
    const range = document.getElementById("text-size");
    if (!range) return;
    range.value = String(size);
    const [name, pct] = SIZES[size];
    range.setAttribute("aria-valuetext", `${name}, ${pct}%`);
    document.getElementById("text-size-val").textContent = name;
    document.getElementById("bold-text").checked = bold;
    document.getElementById("more-contrast").checked = more();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const range = document.getElementById("text-size");
    if (!range) return;
    sync();
    range.addEventListener("input", () => {
      size = Math.min(SIZES.length - 1, Math.max(0, Math.round(Number(range.value)) || 0));
      write(KEY.size, String(size));
      apply();
      sync();
    });
    document.getElementById("bold-text").addEventListener("change", e => {
      bold = e.target.checked;
      write(KEY.bold, bold ? "on" : "off");
      apply();
    });
    document.getElementById("more-contrast").addEventListener("change", e => {
      contrast = e.target.checked ? "on" : "off";
      write(KEY.contrast, contrast);
      apply();
    });
  });
})();
