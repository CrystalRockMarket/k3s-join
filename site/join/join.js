/* Ask-for-a-join-code form. Same-origin only: the intake service is proxied on
   this host at /api/v1/forms/k3s-join, so no cross-origin request and no key in
   the browser. The CSRF token is a double-submit cookie the service sets. */
(function () {
  "use strict";

  var SOURCE = "k3s-join";
  var form = document.getElementById("join-form");
  if (!form) { return; }
  var status = document.getElementById("status");
  var button = document.getElementById("submit");
  var token = "";
  var started = Date.now();

  function say(text, kind) {
    status.textContent = text;
    status.className = "form-status " + (kind || "");
  }

  function err(field, message) {
    var input = document.getElementById(field);
    if (!input) { return; }
    input.setAttribute("aria-invalid", "true");
    if (message) { input.setCustomValidity(message); }
  }

  function clearErrors() {
    Array.prototype.forEach.call(form.querySelectorAll("[aria-invalid]"), function (el) {
      el.removeAttribute("aria-invalid");
      el.setCustomValidity("");
    });
  }

  function reference() {
    var bytes = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return "k3s-" + Array.prototype.map.call(bytes, function (b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }

  function fetchToken() {
    return fetch("/api/v1/forms/" + SOURCE + "/token", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store"
    }).then(function (res) {
      if (!res.ok) { throw new Error("token " + res.status); }
      return res.json();
    }).then(function (data) {
      token = data.token || "";
      if (!token) { throw new Error("no token"); }
    });
  }

  function payload() {
    return {
      name: form.name.value.trim(),
      email: form.email.value.trim(),
      os: form.os.value,
      tier: form.tier.value,
      machine: form.machine.value.trim(),
      specs: form.specs.value.trim(),
      body: form.body.value.trim(),
      node_name: form.machine.value.trim(),
      _hp: form._hp.value,
      reference: form.dataset.reference
    };
  }

  function esc(t) {
    return String(t).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function commandFor(platform, code) {
    if (platform === "linux") {
      return "curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --code '" + code + "'";
    }
    if (platform === "macos") {
      return "curl -fsSL https://k3s.kituwa.com/install-macos.sh | bash -s -- --code '" + code + "'";
    }
    return "$env:KITUWA_JOIN_CODE='" + code + "'; irm https://k3s.kituwa.com/install.ps1 | iex";
  }

  function detectOS() {
    var ua = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent || "";
    if (/win/i.test(ua)) { return "windows"; }
    if (/mac|iphone|ipad/i.test(ua)) { return "macos"; }
    return "linux";
  }

  function nodeCommand(code) {
    return "curl -fsSL https://k3s.kituwa.com/cli/run.sh | sudo bash -s -- " +
           "agent --code '" + code + "'";
  }

  function showCode(data) {
    form.hidden = true;
    var mine = detectOS();
    var order = [mine].concat(["linux", "macos", "windows"].filter(function (p) { return p !== mine; }));
    var label = { linux: "Linux", macos: "macOS", windows: "Windows (PowerShell, as admin)" };

    var panel = document.createElement("div");
    panel.className = "code-panel";
    panel.innerHTML =
      '<p class="code-title">Your install command for <strong>' + esc(data.node) + "</strong></p>" +
      '<p class="note">One line. The join code is inside it — you paste the command, not two things. ' +
      "It expires in " + Math.round((data.expires_in || 3600) / 60) +
      " minutes and the machine can be revoked at any time.</p>" +
      order.map(function (platform, i) {
        return "<details class=\"os-line\"" + (i === 0 ? " open" : "") + ">" +
          "<summary>" + (i === 0 ? "Detected: " : "") + label[platform] + "</summary>" +
          "<pre><code>" + esc(commandFor(platform, data.code)) + "</code></pre>" +
          '<button type="button" class="btn ' + (i === 0 ? "primary" : "") +
          ' small" data-copy="' + platform + '">Copy the command</button>' +
          '<p class="node-alt"><span>Node 18+ installed?</span> A nicer installer, same job:<br>' +
          "<code>" + esc(nodeCommand(data.code)) + "</code></p>" +
          "</details>";
      }).join("") +
      '<p class="form-status good">That is everything. The machine appears on ' +
      '<a href="/points/">the points chart</a> by name once it is idle.</p>' +
      (data.renew ? '<p class="note">Code expired before you got to it? ' +
        '<button type="button" class="btn small" id="renew-code">Get a new code</button> ' +
        '— same machine, no form to fill in.</p>' : '');
    form.parentNode.insertBefore(panel, form.nextSibling);
    status.textContent = "";

    function copy(text, button) {
      var original = button.textContent;
      var done = function () { button.textContent = "Copied"; setTimeout(function () { button.textContent = original; }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      }
    }
    var renewButton = panel.querySelector("#renew-code");
    if (renewButton && data.renew) {
      renewButton.addEventListener("click", function () {
        renewButton.disabled = true;
        renewButton.textContent = "Minting…";
        fetch("/api/v1/enroll/renew", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ renew: data.renew })
        }).then(function (res) { return res.json(); }).then(function (next) {
          if (!next || !next.ok || !next.code) { throw new Error(next && next.error || "could not renew"); }
          panel.remove();
          showCode(next);
        }).catch(function (err) {
          renewButton.disabled = false;
          renewButton.textContent = "Get a new code";
          renewButton.insertAdjacentHTML("afterend",
            ' <span class="bad">' + (err.message || "failed") + '</span>');
        });
      });
    }

    Array.prototype.forEach.call(panel.querySelectorAll("[data-copy]"), function (button) {
      button.addEventListener("click", function () {
        copy(commandFor(button.getAttribute("data-copy"), data.code), button);
      });
    });
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearErrors();
    if (!form.reportValidity()) { return; }
    if (!form.dataset.reference) {
      form.dataset.reference = reference();
    }

    button.disabled = true;
    say("Sending…", "busy");

    var ready = token ? Promise.resolve() : fetchToken();
    ready.then(function () {
      return fetch("/api/v1/forms/" + SOURCE + "/enroll", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Kituwa-Form-Token": token
        },
        body: JSON.stringify(payload())
      });
    }).then(function (res) {
      if (res.status === 201) {
        return res.json().then(function (data) {
          if (data && data.ok && data.code) {
            showCode(data);
            return { done: true };
          }
          throw new Error(data && data.error ? data.error : "no code returned");
        });
      }
      if (res.status === 409 || res.status === 429 || res.status === 503) {
        // Enrollment is closed, full or broken: the request still belongs in the
        // high-priority inbox, so fall back to the normal submission.
        return fetch("/api/v1/forms/" + SOURCE, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-Kituwa-Form-Token": token
          },
          body: JSON.stringify(payload())
        });
      }
      return res;
    }).then(function (res) {
      if (res && res.done) { return { done: true }; }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.ok && data.ok) { return data; }
        if (res.status === 403) {
          token = "";
          throw new Error("Please press send once more.");
        }
        if (res.status === 429) {
          throw new Error("Too many requests from this network. Try again later.");
        }
        throw new Error(data.error || "Something went wrong.");
      });
    }).then(function (data) {
      if (data && data.done) { return; }
      form.hidden = true;
      say("Received — it is in the high-priority inbox. You will get a reply by " +
          "email, and a join code if the machine is a fit. Reference #" + data.id + ".",
          "good");
      var note = document.createElement("p");
      note.className = "note";
      note.innerHTML = "Nothing else to do. Questions? " +
        '<a href="https://services.kituwa.com/quote">Write again</a>.';
      form.parentNode.insertBefore(note, form.nextSibling);
    }).catch(function (error) {
      button.disabled = false;
      say(error.message || "Something went wrong. Please try again.", "bad");
    });
  });

  fetch("/api/v1/enroll/status", { cache: "no-store" }).then(function (r) { return r.json(); })
    .then(function (d) {
      var hint = document.getElementById("enroll-hint");
      if (!hint) { return; }
      if (d.enabled && d.remaining > 0) {
        hint.textContent = "Self-serve: you get your join code on this screen, " +
          d.remaining + " of " + d.capacity + " volunteer slots open.";
      } else if (d.enabled) {
        hint.textContent = "The cluster is full right now — send the form and I will reply by email.";
      } else {
        hint.textContent = "Send the form and I will reply by email with a join code.";
      }
    }).catch(function () {});

  fetchToken().then(function () {
    button.disabled = false;
  }).catch(function () {
    say("The form could not start (no token). Reload the page once.", "bad");
    button.disabled = true;
  });
})();
