// Runs before the first paint: the theme class and the document language. Keep the keys and the
// rules in step with src/shared/lib/theme.ts and src/shared/i18n/detect.ts.
(function () {
  var theme = "system";
  var saved = null;
  try {
    var savedTheme = window.localStorage.getItem("re.theme");
    if (savedTheme === "light" || savedTheme === "dark") theme = savedTheme;
    saved = window.localStorage.getItem("re.lang");
  } catch (e) {
    // storage blocked: follow the system and the browser
  }
  var dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);

  var language = saved === "es" || saved === "en" ? saved : null;
  var preferred =
    navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
  for (var i = 0; language === null && i < preferred.length; i += 1) {
    var primary = String(preferred[i]).toLowerCase().split("-")[0];
    if (primary === "es" || primary === "en") language = primary;
  }
  document.documentElement.lang = language || "es";
})();
