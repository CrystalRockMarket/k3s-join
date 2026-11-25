/* Kituwa Compute Points — reads the published snapshot and draws it.
   No dependencies, no third-party script, and a static SVG fallback when the
   JSON cannot be read (worker offline, first publish not done yet). */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var state = { data: null, range: "history_90d", view: "points" };

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function fmt(n) {
    if (n == null) { return "—"; }
    if (n >= 1e6) { return (n / 1e6).toFixed(2) + "M"; }
    if (n >= 1e4) { return Math.round(n / 1e3) + "k"; }
    // Points are dollars now. Show four decimals under $1 so a laptop's
    // first cents do not render as 0.00.
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
    if (!then) { return "unknown"; }
    var mins = Math.round((Date.now() - then) / 60000);
    if (mins < 1) { return "just now"; }
    if (mins < 60) { return mins + " min ago"; }
    if (mins < 1440) { return Math.round(mins / 60) + " h ago"; }
    return Math.round(mins / 1440) + " d ago";
  }

  function cumulative(buckets, backlog) {
    var running = backlog || 0, out = [];
    buckets.forEach(function (b) { running += b.points; out.push(running); });
    return out;
  }

  function drawPoints(buckets, backlog) {
    var host = document.getElementById("chart");
    host.innerHTML = "";
    if (!buckets.length) {
      host.innerHTML = '<p class="note">The ledger has no samples yet.</p>';
      return;
    }
    // A balance may only contain measured work. Missing source means measured:
    // only an explicit "extrapolated" bucket is excluded.
    var measuredOnly = buckets.filter(function (b) { return b.source !== "extrapolated"; });
    if (!measuredOnly.length) {
      host.innerHTML = '<p class="note">No measured hours yet — the meter starts collecting immediately.</p>';
      return;
    }
    buckets = measuredOnly;
    var balanceValues = cumulative(buckets, backlog);
    var series = [{ label: "balance", values: balanceValues, main: true }];
    var tail = buckets.length;
    ((state.data.history_90d || {}).node_series || []).forEach(function (n) {
      var values = n.cumulative.slice(Math.max(0, n.cumulative.length - tail));
      series.push({ label: n.node, values: values, main: false, node: true });
    });

    var w = host.clientWidth || 900, h = 280, pad = 44, bottom = 26;
    var basis = balanceValues;
    var hi = Math.max.apply(null, basis), lo = Math.min.apply(null, basis);
    var span = (hi - lo) || 1;
    var spanX = Math.max(1, buckets.length - 1);
    var x = function (i) { return pad + (i * (w - pad * 2)) / spanX; };
    var y = function (v) { return h - bottom - ((v - lo) / span) * (h - pad - bottom); };

    var svg = el("svg", { viewBox: "0 0 " + w + " " + h, class: "chart-svg", role: "img",
      "aria-label": "Cumulative points per machine" });
    [0, 0.5, 1].forEach(function (f) {
      var gy = pad + f * (h - pad - bottom);
      svg.appendChild(el("line", { x1: pad, x2: w - pad, y1: gy, y2: gy, stroke: "#232a33" }));
      var label = el("text", { x: 4, y: gy + 4, fill: "#6e7887", "font-size": "11",
        "font-family": "ui-monospace, monospace" });
      label.textContent = fmt(hi - f * span);
      svg.appendChild(label);
    });

    series.forEach(function (line, idx) {
      var d = "", started = false;
      line.values.forEach(function (v, i) {
        if (v == null) { return; }
        d += (started ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
        started = true;
      });
      if (!d) { return; }
      svg.appendChild(el("path", { d: d, fill: "none",
        stroke: line.main ? "#b8f24a" : line.dashed ? "rgba(184,242,74,.35)" : ["#7aa7ff", "#ffb454", "#ff7a9e"][idx % 3],
        "stroke-width": line.main ? 2.6 : 1.8,
        "stroke-dasharray": line.dashed ? "5 5" : "",
        "stroke-linejoin": "round", "stroke-linecap": "round" }));
    });

    var legend = el("text", { x: pad, y: h - 8, fill: "#6e7887", "font-size": "11",
      "font-family": "ui-monospace, monospace" });
    legend.textContent = series.map(function (l) {
      var lastValue = null;
      for (var i = l.values.length - 1; i >= 0; i--) { if (l.values[i] != null) { lastValue = l.values[i]; break; } }
      return l.label + " " + fmt(lastValue || 0);
    }).join("   ");
    svg.appendChild(legend);
    host.appendChild(svg);
    host.setAttribute("data-state", "ready");
  }

  function drawWork(buckets) {
    var host = document.getElementById("chart");
    host.innerHTML = "";
    if (!buckets.length) {
      host.innerHTML = '<p class="note">The ledger has no samples yet.</p>';
      return;
    }
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
      "aria-label": "Work per " + (daily ? "day" : "hour") });
    [0, 0.5, 1].forEach(function (f) {
      var gy = pad + f * (h - pad - bottom);
      svg.appendChild(el("line", { x1: pad, x2: w - pad, y1: gy, y2: gy, stroke: "#232a33" }));
    });
    points.forEach(function (p, i) {
      var x = pad + i * ((w - pad * 2) / points.length);
      var bh = (p.points / hi) * (h - pad - bottom);
      svg.appendChild(el("rect", { x: x, y: h - bottom - (bh || 2), width: Math.max(1, barW),
        height: bh || 2, fill: bh < 1 ? "#2d3641" : (p.measured ? "#b8f24a" : "rgba(184,242,74,.34)") }));
    });
    var measuredCount = points.filter(function (p) { return p.measured; }).length;
    var label = el("text", { x: pad, y: h - 8, fill: "#6e7887", "font-size": "11",
      "font-family": "ui-monospace, monospace" });
    label.textContent = "measured " + measuredCount + (measuredCount === points.length ? "" : " · estimated " + (points.length - measuredCount));
    svg.appendChild(label);
    host.appendChild(svg);
    host.setAttribute("data-state", "ready");
  }

  function drawChart() {
    var buckets = seriesFor(state.data);
    if (state.view === "points") {
      drawPoints(buckets, state.data.backlog_points || 0);
    } else {
      drawWork(buckets);
    }
  }

  function renderNodes(nodes) {
    var host = document.getElementById("nodes");
    if (!nodes || !nodes.length) { return; }
    host.innerHTML = nodes.map(function (n) {
      return '<article class="card">' +
        '<p class="card-label">' + (n.role === "dedicated" || n.node === "trinity-nc-server" ? "Dedicated worker" : "Worker") + '</p>' +
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
      fmt(d.total_points) + " measured on the worker · " + fmt(d.points_24h) + " in the last 24 h";
    document.getElementById("asof").textContent =
      "Published " + ago(d.generated_at) + " · " + d.points_24h + " points in the last 24 h · " +
      "rule: " + d.rule;
    renderNodes(d.nodes);

    try {
      drawChart();
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

  ["points", "work"].forEach(function (view) {
    document.getElementById("view-" + view).addEventListener("click", function () {
      state.view = view;
      ["points", "work"].forEach(function (v) {
        document.getElementById("view-" + v).setAttribute("aria-selected", v === view ? "true" : "false");
      });
      var range = document.getElementById("tab-90").closest(".tabs");
      if (range) { range.style.opacity = view === "points" ? ".45" : "1"; }
      paint();
    });
  });
  window.addEventListener("resize", function () {
    if (!state.data) { return; }
    try {
      drawChart();
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
