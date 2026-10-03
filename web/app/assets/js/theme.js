// Applied synchronously in <head> to avoid a flash of the wrong theme.
(function () {
  try {
    var t = localStorage.getItem('vkm.theme');
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch (e) { /* storage unavailable */ }
})();
