(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function select(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      var panel = document.getElementById(t.getAttribute('aria-controls'));
      if (panel) { panel.hidden = !on; }
    });
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function () { select(t); });
    t.addEventListener('keydown', function (e) {
      var i = tabs.indexOf(t);
      if (e.key === 'ArrowRight') { select(tabs[(i + 1) % tabs.length]); tabs[(i + 1) % tabs.length].focus(); }
      if (e.key === 'ArrowLeft') { var p = (i - 1 + tabs.length) % tabs.length; select(tabs[p]); tabs[p].focus(); }
    });
  });
})();

/* Copy buttons anywhere on the page: the text they name, in full. */
document.addEventListener("click", function (e) {
  var b = e.target.closest("[data-copy]");
  if (!b) { return; }
  var target = document.getElementById(b.getAttribute("data-copy"));
  if (!target) { return; }
  var text = target.textContent;
  var done = function () { var old = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = old; }, 1500); };
  if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done, function () {}); }
});
