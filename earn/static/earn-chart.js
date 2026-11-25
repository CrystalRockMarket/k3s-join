/* Earn — live points per machine. Reads the published ledger snapshot; no
   third-party chart library and no Grafana embed. Stale data stays readable. */
(function () {
  "use strict";

  var host = document.getElementById("earn-chart");
  if (!host) { return; }
  var note = document.getElementById("earn-chart-note");
  var buttons = Array.prototype.slice.call(
    document.querySelectorAll("#earn-range [data-range]")
  );
  var state = { data: null, range: "7d", at: 0 };
  var COLORS = ["#b8f24a", "#7aa7ff", "#ffb454", "#ff7a9e", "#8de0c0", "#c9a7ff"];

  function fmt(n) {
    if (n == null) { return "—"; }
    if (Math.abs(n) < 1) {
      return Number(n).toLocaleString("en-US", {
        minimumFractionDigits: 4, maximumFractionDigits: 4
      });
    }
    return Number(n).toLocaleString("en-US", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function ago(secs) {
    secs = Math.max(0, Math.round(secs));
    if (secs < 90) { return secs + "s ago"; }
    return Math.round(secs / 60) + " min ago";
  }

  function daysFor(range) {
    return { "1d": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365 }[range] || 7;
  }

  function sourceFor(range) {
    if (!state.data) { return null; }
    return range === "1y"
      ? (state.data.history_365d || state.data.history_90d)
      : state.data.history_90d;
  }

  function filterSeries(src, days) {
    var cutoff = Date.now() - days * 86400000;
    var keep = [];
    (src.buckets || []).forEach(function (b, i) {
      if (new Date(b.t).getTime() >= cutoff) { keep.push(i); }
    });
    var buckets = keep.map(function (i) { return src.buckets[i]; });
    var series = (src.node_series || []).map(function (n, i) {
      return {
        node: n.node,
        values: keep.map(function (j) { return Number(n.cumulative[j] || 0); }),
        color: COLORS[i % COLORS.length]
      };
    });
    return { buckets: buckets, series: series };
  }

  function empty(message) {
    host.innerHTML = '<p class="note">' + message + "</p>";
    host.setAttribute("data-state", "empty");
  }

  function draw() {
    if (!state.data) { return; }
    var src = sourceFor(state.range);
    if (!src || !src.buckets || !src.buckets.length) {
      empty("The ledger has no samples yet.");
      return;
    }
    var set = filterSeries(src, daysFor(state.range));
    if (set.buckets.length < 2) {
      empty("No measured hours in this range yet. The meter starts collecting immediately.");
      return;
    }

    var w = 920, h = 330, pad = 52, bottom = 34, top = 18;
    var max = 0;
    set.series.forEach(function (s) {
      s.values.forEach(function (v) { if (v > max) { max = v; } });
    });
    max = max || 0.0001;
    var spanY = max * 1.12;
    var plotW = w - pad * 2;
    var plotH = h - top - bottom;
    var n = set.buckets.length;

    function x(i) { return pad + (i * plotW) / Math.max(1, n - 1); }
    function y(v) { return top + plotH - (v / spanY) * plotH; }

    var NS = "http://www.w3.org/2000/svg";
    function el(name, attrs) {
      var node = document.createElementNS(NS, name);
      Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
      return node;
    }

    var svg = el("svg", {
      viewBox: "0 0 " + w + " " + h,
      class: "chart-svg",
      role: "img",
      "aria-label": "Points earned by machine, " + state.range
    });

    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var gy = top + f * plotH;
      svg.appendChild(el("line", {
        x1: pad, x2: w - pad, y1: gy, y2: gy, stroke: "#232a33"
      }));
      var label = el("text", {
        x: 4, y: gy + 4, fill: "#6e7887", "font-size": "11",
        "font-family": "ui-monospace, monospace"
      });
      label.textContent = fmt((1 - f) * spanY);
      svg.appendChild(label);
    });

    set.series.forEach(function (s) {
      var d = "";
      var started = false;
      s.values.forEach(function (v, i) {
        if (v == null) { return; }
        d += (started ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(2);
        started = true;
      });
      if (!d) { return; }
      svg.appendChild(el("path", {
        d: d, fill: "none", stroke: s.color, "stroke-width": "2.4",
        "stroke-linejoin": "round", "stroke-linecap": "round"
      }));
      var last = s.values[s.values.length - 1];
      svg.appendChild(el("circle", {
        cx: x(n - 1).toFixed(1), cy: y(last).toFixed(2), r: "3.2",
        fill: s.color, stroke: "#0d0f12", "stroke-width": "1.5"
      }));
    });

    host.innerHTML = "";
    host.appendChild(svg);

    var legend = document.createElement("div");
    legend.className = "chart-legend";
    set.series.forEach(function (s) {
      var item = document.createElement("span");
      item.className = "legend-item";
      var dot = document.createElement("i");
      dot.style.background = s.color;
      item.appendChild(dot);
      item.appendChild(document.createTextNode(
        s.node + " " + fmt(s.values[s.values.length - 1])
      ));
      legend.appendChild(item);
    });
    host.appendChild(legend);
    host.setAttribute("data-state", "ready");

    if (note) {
      note.textContent = "Updated " + ago((Date.now() - state.at) / 1000) +
        " · 1 point = $1 of Kituwa credit · measured work only";
    }
  }

  function select(range) {
    state.range = range;
    buttons.forEach(function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-range") === range ? "true" : "false");
    });
    draw();
  }

  function load() {
    fetch("/points/points.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(function (d) {
        state.data = d;
        state.at = Date.now();
        draw();
      })
      .catch(function () {
        if (!state.data) {
          empty("The worker is offline — this chart will fill from the last published snapshot.");
        }
      });
  }

  buttons.forEach(function (b) {
    b.addEventListener("click", function () { select(b.getAttribute("data-range")); });
  });
  var initial = buttons.filter(function (b) { return b.getAttribute("aria-selected") === "true"; })[0];
  if (initial) { state.range = initial.getAttribute("data-range"); }

  load();
  setInterval(load, 15000);
})();
