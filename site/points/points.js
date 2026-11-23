/* Kituwa Compute Points — reads the published snapshot and draws it.
   No dependencies, no third-party script, and a static SVG fallback when the
   JSON cannot be read (worker offline, first publish not done yet). */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var state = { data: null, range: "history_90d" };

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
    var buckets = (series || []).slice();
    if (!buckets.length) {
      host.innerHTML = '<p class="note">The ledger has no samples yet.</p>';
      host.setAttribute("data-state", "empty");
      return;
    }
    // Three windows: hourly for a day and a week, daily for the quarter.
    var daily = state.range === "history_90d";
    var points = [];
    if (daily) {
      var byDay = {};
      buckets.forEach(function (b) {
        var d = b.t.slice(0, 10);
        byDay[d] = byDay[d] || { points: 0, measured: false };
        byDay[d].points += b.points;
        if (b.source === "measured") { byDay[d].measured = true; }
      });
      points = Object.keys(byDay).sort().map(function (d) {
        return { label: d, points: byDay[d].points, measured: byDay[d].measured };
      });
    } else {
      points = buckets.map(function (b) {
        return { label: b.t, points: b.points, measured: b.source !== "extrapolated" };
      });
    }

    var w = host.clientWidth || 900, h = 260, pad = 34, bottom = 26;
    var hi = Math.max.apply(null, points.map(function (p) { return p.points; })) || 1;
    var barW = Math.max(1, (w - pad * 2) / points.length - (daily ? 1 : 0.4));

    var svg = el("svg", { viewBox: "0 0 " + w + " " + h, class: "chart-svg", role: "img",
      "aria-label": "Work per " + (daily ? "day" : "hour") + ", measured and extrapolated" });

    [0, 0.5, 1].forEach(function (f) {
      var gy = pad + f * (h - pad - bottom);
      svg.appendChild(el("line", { x1: pad, x2: w - pad, y1: gy, y2: gy, stroke: "#232a33" }));
    });
    svg.appendChild(el("line", { x1: pad, x2: w - pad, y1: h - bottom, y2: h - bottom, stroke: "#2d3641" }));

    points.forEach(function (p, i) {
      var x = pad + i * ((w - pad * 2) / points.length);
      var bh = (p.points / hi) * (h - pad - bottom);
      if (bh < 1) {
        // An idle hour/day still gets a tick, so "off" is visible rather than blank.
        svg.appendChild(el("rect", { x: x, y: h - bottom - 2, width: Math.max(1, barW),
          height: 2, fill: "#2d3641" }));
        return;
      }
      svg.appendChild(el("rect", { x: x, y: h - bottom - bh, width: Math.max(1, barW),
        height: bh, fill: p.measured ? "#b8f24a" : "rgba(184,242,74,.34)" }));
    });

    var measuredCount = points.filter(function (p) { return p.measured; }).length;
    var label = el("text", { x: pad, y: h - 8, fill: "#6e7887", "font-size": "11",
      "font-family": "ui-monospace, monospace" });
    label.textContent = "measured " + measuredCount + " · extrapolated " + (points.length - measuredCount);
    svg.appendChild(label);

    var maxLabel = el("text", { x: w - pad, y: h - 8, fill: "#6e7887", "font-size": "11",
      "text-anchor": "end", "font-family": "ui-monospace, monospace" });
    maxLabel.textContent = "peak " + fmt(hi) + " / " + (daily ? "day" : "hour");
    svg.appendChild(maxLabel);
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
        (n.share != null ? '<p class="note">' + n.share + '% of the measured work</p>' : '') +
        '<p class="note">last sample ' + ago(n.last_seen) + '</p>' +
        '</article>';
    }).join("");
  }

  function seriesFor(d) {
    if (state.range === "history_90d") {
      return (d.history_90d || {}).buckets || [];
    }
    var s = d[state.range];
    if (!s || !s.length) { return (d.history_90d || {}).buckets || []; }
    return s;
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
    var series = seriesFor(d);
    try {
      draw(series, d.total_points);
    } catch (err) {
      var host = document.getElementById("chart");
      host.setAttribute("data-state", "error");
      host.textContent = "Chart error: " + (err && err.message ? err.message : err);
    }
  }

  function setRange(range, which) {
    state.range = range;
    ["tab-24", "tab-7d", "tab-90"].forEach(function (id) {
      document.getElementById(id).setAttribute("aria-selected", id === which ? "true" : "false");
    });
    paint();
  }

  document.getElementById("tab-24").addEventListener("click", function () { setRange("series_24h", "tab-24"); });
  document.getElementById("tab-7d").addEventListener("click", function () { setRange("series_7d", "tab-7d"); });
  document.getElementById("tab-90").addEventListener("click", function () { setRange("history_90d", "tab-90"); });
  window.addEventListener("resize", function () {
    if (!state.data) { return; }
    try {
      draw(seriesFor(state.data), state.data.total_points);
    } catch (err) {
      // A resize must never leave the chart blank.
      document.getElementById("chart").textContent = "Chart error: " + (err && err.message);
    }
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
