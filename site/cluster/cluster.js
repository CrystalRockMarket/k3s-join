(function () {
  "use strict";

  function fmt(n) {
    if (n == null) { return "—"; }
    if (n >= 1e6) { return (n / 1e6).toFixed(2) + "M"; }
    if (n >= 1e4) { return Math.round(n / 1e3) + "k"; }
    // Points are dollars now; keep four decimals under $1 so a laptop's first
    // cents do not render as 0.00.
    if (Math.abs(n) < 1) {
      return Number(n).toLocaleString("en-US", {
        minimumFractionDigits: 4, maximumFractionDigits: 4
      });
    }
    return Number(n).toLocaleString("en-US", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function ago(iso) {
    var then = new Date(iso).getTime();
    if (!then) { return "—"; }
    var secs = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (secs < 90) { return secs + "s ago"; }
    var mins = Math.round(secs / 60);
    if (mins < 60) { return mins + " min ago"; }
    if (mins < 1440) { return Math.round(mins / 60) + " h ago"; }
    return Math.round(mins / 1440) + " d ago";
  }

  function statusOf(n) {
    if (n.pending) { return "pending"; }
    if (n.online === false || n.ready === false) { return "offline"; }
    return n.working ? "working" : "online";
  }

  function statusPill(n) {
    var state = statusOf(n);
    return '<span class="pill ' + state + '">' + state + "</span>";
  }

  // The ledger keeps the member-facing name; k3s derives its node name from the
  // hostname. Both are the same machine, so map one to the other instead of
  // showing it twice.
  var ALIASES = { trinity: "trinity-nc-server" };
  function canonical(name) { return ALIASES[name] || name; }

  function mergeNodes(d) {
    var nodes = (d.nodes || []).map(function (n) {
      var copy = Object.assign({}, n);
      copy.node = canonical(copy.node);
      return copy;
    });
    var known = {};
    nodes.forEach(function (n) { known[n.node] = true; });
    (d.joined || []).forEach(function (j) {
      var name = canonical(j.node);
      if (name === "kituwa-cloud-1") { return; }   // control plane, not a worker
      if (known[name]) { return; }
      nodes.push({
        node: name,
        role: j.volunteer ? "volunteer" : "dedicated",
        points: 0,
        last_seen: d.generated_at,
        pending: true,
        online: j.ready !== false
      });
      known[name] = true;
    });
    return nodes;
  }

  function render(d) {
    var nodes = mergeNodes(d);
    var total = nodes.reduce(function (a, n) { return a + (n.points || 0); }, 0);
    var online = nodes.filter(function (n) { return statusOf(n) !== "offline"; }).length;

    document.getElementById("s-nodes").textContent = online + " / " + nodes.length;
    document.getElementById("s-total").textContent = fmt(d.total_points != null ? d.total_points : total);
    var day = d.points_24h != null ? d.points_24h : d.points_10m;
    document.getElementById("s-24h").textContent = fmt(day);

    document.getElementById("rows").innerHTML = nodes.map(function (n) {
      var share = n.share;
      if (share == null && total && n.points) {
        share = Math.round((n.points / total) * 1000) / 10;
      }
      return "<tr>" +
        '<td class="worker-cell"><strong>' + n.node + "</strong></td>" +
        "<td>" + (n.role === "volunteer" ? "volunteer" : "dedicated") + "</td>" +
        "<td>" + statusPill(n) + "</td>" +
        "<td><strong>" + fmt(n.points) + "</strong></td>" +
        "<td>" + (share == null ? "—" : share + "%") + "</td>" +
        "<td>" + ago(n.last_seen) + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="note">No workers have reported yet.</td></tr>';
  }

  function load() {
    fetch("/points/cluster.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(function (d) {
        generatedAt = Date.parse(d.generated_at || "") || Date.now();
        render(d);
      })
      .catch(function () {
        document.getElementById("rows").innerHTML =
          '<tr><td colspan="6" class="note">The worker is offline — this is the last published snapshot.</td></tr>';
      });
  }

  // Live tail: a small snapshot every 5 s. Merge by node name so a machine that
  // joined but has no points yet appears once, as pending.
  var generatedAt = Date.now();
  function poll() {
    fetch("/points/live.json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        generatedAt = Date.parse(d.generated_at || "") || generatedAt;
        render(d);
      })
      .catch(function () {});
  }

  setInterval(function () {
    var el = document.getElementById("s-next");
    if (!el) { return; }
    el.textContent = ago(new Date(generatedAt).toISOString());
  }, 1000);

  load();
  poll();
  setInterval(poll, 5000);
})();
