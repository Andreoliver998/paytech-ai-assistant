// Compatibility shim.
// Some docs/links may still reference `frontend/script.js`. Keep it working by loading `app.js`.
(function () {
  try {
    var s = document.createElement("script");
    // Keep cache-busting aligned with index.html.
    s.src = "app.js?v=20260210_025";
    s.defer = true;
    document.head.appendChild(s);
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error("[paytech] failed to load app.js from script.js shim", e);
  }
})();
