/* Tabs, OS auto-detect and copy buttons. Shared by every page: the static
   install section calls initTabs on load, and join.js calls it again for the
   code panel it inserts after a code is minted. */
(function () {
  "use strict";

  function detectOS() {
    var ua = (navigator.userAgentData && navigator.userAgentData.platform) ||
             navigator.platform || navigator.userAgent || "";
    if (/win/i.test(ua)) { return "windows"; }
    if (/mac|iphone|ipad/i.test(ua)) { return "macos"; }
    return "linux";
  }

  /* root is a [data-os-tabs] element: one tablist plus one panel per tab. */
  function initTabs(root) {
    if (!root) { return null; }
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
    if (!tabs.length) { return null; }

    function select(tab) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.setAttribute("tabindex", on ? "0" : "-1");
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) { panel.hidden = !on; }
      });
    }

    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t); });
      t.addEventListener("keydown", function (e) {
        var next = null;
        if (e.key === "ArrowRight") { next = tabs[(i + 1) % tabs.length]; }
        if (e.key === "ArrowLeft") { next = tabs[(i - 1 + tabs.length) % tabs.length]; }
        if (e.key === "Home") { next = tabs[0]; }
        if (e.key === "End") { next = tabs[tabs.length - 1]; }
        if (next) { e.preventDefault(); select(next); next.focus(); }
      });
    });

    /* Copy buttons in this group name their platform; this is how a copy
       button moves to its own tab. */
    root.selectTab = function (os) {
      var tab = null;
      tabs.forEach(function (t) { if (t.getAttribute("data-os") === os) { tab = t; } });
      if (tab) { select(tab); }
    };

    var initial = null;
    if (root.getAttribute("data-os-pick") === "auto") {
      var mine = detectOS();
      tabs.forEach(function (t) { if (t.getAttribute("data-os") === mine) { initial = t; } });
    }
    select(initial || tabs[0]);
    return root;
  }

  window.Kituwa = window.Kituwa || {};
  window.Kituwa.initTabs = initTabs;
  window.Kituwa.detectOS = detectOS;

  Array.prototype.forEach.call(document.querySelectorAll("[data-os-tabs]"), initTabs);

  /* Copy buttons anywhere on the page: the text they name, in full. A button
     that sits in a tab group first moves the page to that button's tab. */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) { return; }
    var group = b.closest("[data-os-tabs]");
    if (group && group.selectTab && b.getAttribute("data-os")) {
      group.selectTab(b.getAttribute("data-os"));
    }
    var target = document.getElementById(b.getAttribute("data-copy"));
    if (!target) { return; }
    var text = target.textContent;
    var done = function () { var old = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = old; }, 1500); };
    if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done, function () {}); }
  });
})();
