/* Kituwa Compute Points — reads the published snapshot and draws it.
   No dependencies, no third-party script, and a static SVG fallback when the
   JSON cannot be read (worker offline, first publish not done yet). */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var state = { data: null, range: "series_24h" };

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function fmt(n) {
    if (n == null) { return "—"; }
    if (n >= 1e6) { return (n / 1e6).toFixed(2) + "M"; }
    if (n >= 1e4) { return Math.round(n / 1e3) + "k"; }
    return Math.round(n).toLocaleString("en-US");
  }

  function ago(iso) {
    var then = new Date(iso).getTime();
    if (!then) { return "unknown"; }
    var mins = Math.round((Date.now() - then) / 60000);
    if (mins < 1) { return "just now"; }
    if (mins < 60) { return mins + " min ago"; }
    if (mins < 1440) { return Math.round(mins / 60) + " h ago"; }
    return Math.round(mins / 1440) + " d ago";
  }

  function draw(series, total) {
    var host = document.getElementById("chart");
    host.innerHTML = "";
    var pts = (series || []).map(function (p) { return p.total != null ? p.total : p.points; });
    if (pts.length < 2) {
      host.innerHTML = '<p class="note">Not enough history yet — the ledger is ' +
        'still collecting its first samples. Reload in a few minutes.</p>';
      host.setAttribute("data-state", "empty");
      return;
    }
    var w = host.clientWidth || 760, h = 260, pad = 34;
    var lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    var span = (hi - lo) || 1;
    // A tight range must not round every label to the same "34k".
    var axis = function (v) {
      return span < 5000 ? Math.round(v).toLocaleString("en-US") : fmt(v);
    };
    var x = function (i) { return pad + (i * (w - pad * 2)) / (pts.length - 1); };
    var y = function (v) { return h - pad - ((v - lo) / span) * (h - pad * 2); };

    var svg = el("svg", { viewBox: "0 0 " + w + " " + h, class: "chart-svg", role: "img",
      "aria-label": "Cumulative points, " + fmt(lo) + " to " + fmt(hi) });

    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var gy = pad + f * (h - pad * 2);
      svg.appendChild(el("line", { x1: pad, x2: w - pad, y1: gy, y2: gy,
        stroke: "#232a33", "stroke-width": 1 }));
      var label = el("text", { x: 4, y: gy + 4, fill: "#6e7887", "font-size": "11",
        "font-family": "ui-monospace, monospace" });
      label.textContent = axis(hi - f * span);
      svg.appendChild(label);
    });

    var line = pts.map(function (v, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); }).join(" ");
    var area = line + " L" + x(pts.length - 1).toFixed(1) + " " + (h - pad) +
               " L" + x(0).toFixed(1) + " " + (h - pad) + " Z";
    var gradId = "pointsGrad";
    var defs = el("defs", {});
    var grad = el("linearGradient", { id: gradId, x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": "#b8f24a", "stop-opacity": "0.35" }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": "#b8f24a", "stop-opacity": "0" }));
    defs.appendChild(grad);
    svg.appendChild(defs);
    svg.appendChild(el("path", { d: area, fill: "url(#" + gradId + ")" }));
    svg.appendChild(el("path", { d: line, fill: "none", stroke: "#b8f24a",
      "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));

    var last = pts.length - 1;
    svg.appendChild(el("circle", { cx: x(last), cy: y(pts[last]), r: 4.5, fill: "#b8f24a" }));
    host.appendChild(svg);
    host.setAttribute("data-state", "ready");
  }

  function renderNodes(nodes) {
    var host = document.getElementById("nodes");
    if (!nodes || !nodes.length) { return; }
    host.innerHTML = nodes.map(function (n) {
      return '<article class="card">' +
        '<p class="card-label">' + (n.node === "trinity" ? "Dedicated worker" : "Worker") + '</p>' +
        '<h3>' + n.node + '</h3>' +
        '<p class="node-points">' + fmt(n.points) + ' points</p>' +
        '<p class="note">last sample ' + ago(n.last_seen) + '</p>' +
        '</article>';
    }).join("");
  }

  function paint() {
    var d = state.data;
    if (!d) { return; }
    document.getElementById("total").textContent = fmt(d.total_points);
    document.getElementById("score-sub").textContent =
      fmt(d.backlog_points) + " points from the backlog + " + fmt(d.live_points) + " measured live";
    document.getElementById("asof").textContent =
      "Published " + ago(d.generated_at) + " · " + d.points_24h + " points in the last 24 h · " +
      "rule: " + d.rule;
    renderNodes(d.nodes);
    var series = d[state.range];
    if (!series || series.length < 2) {
      // The ledger has not filled an hour yet. Two honest points: the backlog at
      // genesis and the total now.
      series = [{ total: d.backlog_points }, { total: d.total_points }];
    }
    draw(series, d.total_points);
  }

  function setRange(range, which) {
    state.range = range;
    ["tab-24", "tab-7d"].forEach(function (id) {
      document.getElementById(id).setAttribute("aria-selected", id === which ? "true" : "false");
    });
    paint();
  }

  document.getElementById("tab-24").addEventListener("click", function () { setRange("series_24h", "tab-24"); });
  document.getElementById("tab-7d").addEventListener("click", function () { setRange("series_7d", "tab-7d"); });
  window.addEventListener("resize", function () {
    if (state.data) { draw(state.data[state.range], state.data.total_points); }
  });

  fetch("/points/points.json", { cache: "no-store" })
    .then(function (r) { if (!r.ok) { throw new Error("snapshot " + r.status); } return r.json(); })
    .then(function (data) { state.data = data; paint(); })
    .catch(function () {
      var host = document.getElementById("chart");
      host.setAttribute("data-state", "stale");
      host.innerHTML = '<img src="/points/chart.svg" alt="Points over time" class="chart-fallback">';
      document.getElementById("asof").textContent =
        "Showing the last published chart — the live ledger is not reachable right now.";
    });
})();
