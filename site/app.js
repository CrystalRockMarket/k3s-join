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
