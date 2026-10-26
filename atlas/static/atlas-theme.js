/* Blocking on purpose: set data-theme before CSS so the first paint is the right theme. */
(function () {
  "use strict";
  let theme = "";
  try {
    theme = localStorage.getItem("atlas.theme") || "";
  } catch (err) {
    theme = "";
  }
  if (theme !== "light" && theme !== "dark") {
    let dark = false;
    try {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (err2) {
      dark = false;
    }
    theme = dark ? "dark" : "light";
  }
  document.documentElement.dataset.theme = theme;
})();
