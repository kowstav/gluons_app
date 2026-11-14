// Applies a saved light or dark choice before first paint. With no saved choice the system setting applies.
try {
  var theme = localStorage.getItem("gluons.theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch (error) { /* Storage can be unavailable; the system setting still applies. */ }
