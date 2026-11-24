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
      var joinedList = d.joined || [];
      document.getElementById("s-nodes").textContent = joinedList.length
        ? joinedList.filter(function (x) { return x.ready; }).length + " / " + joinedList.length
        : d.online + " / " + d.nodes.length;
      document.getElementById("s-total").textContent = fmt(d.total_points);
      document.getElementById("s-24h").textContent = fmt(d.points_24h);
      document.getElementById("s-backlog").textContent = fmt(d.backlog_points);
      var known = {};
      d.nodes.forEach(function (n) { known[n.node] = true; });
      (d.joined || []).forEach(function (j) {
        if (!known[j.node]) {
          d.nodes.push({ node: j.node, role: j.volunteer ? "volunteer" : "dedicated",
                         points: 0, last_seen: d.generated_at, pending: true });
        }
      });
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

  // Live tail: poll a tiny snapshot every 5 s and redraw as soon as a worker
  // appears or its points move. The static page keeps working if this stops.
  var seen = {};
  function poll() {
    fetch("/points/live.json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        nextIn = 15;
        var joined = d.joined || [];
        var online = joined.filter(function (x) { return x.ready; }).length;
        document.getElementById("s-nodes").textContent = joined.length ? (online + " / " + joined.length)
          : (d.active_nodes + " / " + d.nodes.length);
        document.getElementById("s-total").textContent = fmt(d.total_points);
        document.getElementById("s-24h").textContent = fmt(d.points_10m);
        var known = {};
      d.nodes.forEach(function (n) { known[n.node] = true; });
      (d.joined || []).forEach(function (j) {
        if (!known[j.node]) {
          d.nodes.push({ node: j.node, role: j.volunteer ? "volunteer" : "dedicated",
                         points: 0, last_seen: d.generated_at, pending: true });
        }
      });
      document.getElementById("rows").innerHTML = d.nodes.map(function (n) {
          var fresh = !seen[n.node];
          seen[n.node] = true;
          var art = n.role === "volunteer" ? "laptop.svg" : "worker.svg";
          return '<tr class="' + (fresh ? "row-new" : "") + '">' +
            '<td class="worker-cell"><img class="px" src="/cluster/' + art + '" width="16" height="16" alt="">' +
            "<strong>" + n.node + "</strong>" +
            '<span class="worker-points">' + fmt(n.points) + " points</span></td>" +
            "<td>" + (n.role === "volunteer" ? "volunteer" : "dedicated") + "</td>" +
            "<td>" + (n.pending ? '<span class="pill">pending · no work yet</span>'
                     : fresh ? '<span class="pill">just joined</span>' : "online") + "</td>" +
            "<td><strong>" + fmt(n.points) + "</strong></td><td>—</td>" +
            "<td>" + ago(n.last_seen) + "</td></tr>";
        }).join("");
      }).catch(function () {});
  }
  var nextIn = 15;
  setInterval(function () {
    nextIn = nextIn > 0 ? nextIn - 1 : 0;
    var el = document.getElementById("s-next");
    if (el) { el.textContent = nextIn > 0 ? "next update in " + nextIn + "s" : "updating…"; }
  }, 1000);
  poll();
  setInterval(poll, 5000);
})();
