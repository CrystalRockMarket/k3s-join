(function () {
  var box = document.getElementById("box");
  var t = new URLSearchParams(location.search).get("t");
  if (!t) { box.innerHTML = '<p class="form-status bad">No link token. Run the installer again to get one.</p>'; return; }
  fetch("/api/v1/account/verify?t=" + encodeURIComponent(t), { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { throw new Error(d.error || "could not verify"); }
      box.innerHTML = '<p class="quick-title">Verified worker <strong>' + d.node + "</strong></p>" +
        '<p class="note">Points for this machine are now attached to ' + d.email + ".</p>" +
        '<p class="score-number">' + (d.points == null ? "—" : Math.round(d.points).toLocaleString("en-US")) + "</p>" +
        '<p class="stat-l">points earned by this worker</p>' +
        '<p class="note"><a href="/cluster/">See it live on the cluster page</a></p>';
    })
    .catch(function (e) {
      box.innerHTML = '<p class="form-status bad">' + (e.message || "could not verify") + "</p>";
    });
})();
