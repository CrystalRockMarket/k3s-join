(function () {
  "use strict";
  function fmt(n) {
    if (n == null) { return "—"; }
    if (n >= 1e6) { return (n / 1e6).toFixed(2) + "M"; }
    if (n >= 1e4) { return Math.round(n / 1e3) + "k"; }
    return Math.round(n).toLocaleString("en-US");
  }
  function ago(iso) {
    var mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (!isFinite(mins)) { return "—"; }
    if (mins < 1) { return "just now"; }
    if (mins < 60) { return mins + " min ago"; }
    if (mins < 1440) { return Math.round(mins / 60) + " h ago"; }
    return Math.round(mins / 1440) + " d ago";
  }
  fetch("/points/cluster.json", { cache: "no-store" })
    .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
    .then(function (d) {
      document.getElementById("s-nodes").textContent = d.online + " / " + d.nodes.length;
      document.getElementById("s-total").textContent = fmt(d.total_points);
      document.getElementById("s-24h").textContent = fmt(d.points_24h);
      document.getElementById("s-backlog").textContent = fmt(d.backlog_points);
      document.getElementById("rows").innerHTML = d.nodes.map(function (n) {
        var art = n.role === "volunteer" ? "laptop.svg" : "worker.svg";
        return "<tr>" +
          '<td class="worker-cell"><img class="px" src="/cluster/' + art + '" width="16" height="16" alt="">' +
          "<strong>" + n.node + "</strong>" +
          '<span class="worker-points">' + fmt(n.points) + " points</span></td>" +
          "<td>" + (n.role === "volunteer" ? "volunteer" : "dedicated") + "</td>" +
          "<td>" + (n.online ? '<span class="pill">online</span>' : "offline") + "</td>" +
          "<td><strong>" + fmt(n.points) + "</strong></td>" +
          "<td>" + n.share + "%</td>" +
          "<td>" + ago(n.last_seen) + "</td></tr>";
      }).join("") || '<tr><td colspan="6" class="note">No workers have reported yet.</td></tr>';
      document.getElementById("sources").innerHTML = d.backlog_sources.map(function (b) {
        return "<tr><td>" + b.source + "</td><td>" + b.units + "</td><td>" + fmt(b.points) + "</td></tr>";
      }).join("");
      document.getElementById("stats").insertAdjacentHTML("afterend",
        '<p class="note">Snapshot taken ' + ago(d.generated_at) + ", published by the worker.</p>");
    })
    .catch(function () {
      document.getElementById("rows").innerHTML =
        '<tr><td colspan="6" class="note">The worker is offline — this page shows the last published snapshot.</td></tr>';
    });
})();
