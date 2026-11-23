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

  function showCode(data) {
    form.hidden = true;
    var panel = document.createElement("div");
    panel.className = "code-panel";
    panel.innerHTML =
      '<p class="code-title">Your join code for <strong>' + esc(data.node) + "</strong></p>" +
      '<p class="note">Shown once and never stored. It expires in ' +
      Math.round((data.expires_in || 3600) / 60) + " minutes, and the node can be revoked.</p>" +
      '<pre><code id="join-code">' + esc(data.code) + "</code></pre>" +
      '<div class="code-actions">' +
      '<button type="button" class="btn primary" id="copy-code">Copy the code</button>' +
      '<button type="button" class="btn" id="copy-linux">Copy the Linux command</button>' +
      "</div>" +
      '<p class="note">Then on the machine:</p>' +
      "<pre><code>" + esc(data.install_linux) + "</code></pre>" +
      '<p class="note">macOS: <code>' + esc(data.install_macos) + "</code></p>" +
      '<p class="form-status good">That is everything. The machine appears on ' +
      '<a href="/points/">the points chart</a> by name once it is idle.</p>';
    status.parentNode.insertBefore(panel, status);
    status.textContent = "";

    function copy(text, button, label) {
      var done = function () { button.textContent = "Copied"; setTimeout(function () { button.textContent = label; }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      }
    }
    panel.querySelector("#copy-code").addEventListener("click", function () {
      copy(data.code, this, "Copy the code");
    });
    panel.querySelector("#copy-linux").addEventListener("click", function () {
      copy(data.install_linux, this, "Copy the Linux command");
    });
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearErrors();
    if (!form.reportValidity()) { return; }
    // A person cannot fill this in under two seconds.
    if (Date.now() - started < 2000) {
      say("That was very fast — give it a second and press send again.", "bad");
      return;
    }
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
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.ok && data.ok) { return data; }
        if (res.status === 403) {
          // The token cookie was refused (expired or a fresh session).
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
      status.parentNode.insertBefore(note, status.nextSibling);
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

  fetchToken().catch(function () {
    say("The form could not start (no token). Reload the page once.", "bad");
    button.disabled = true;
  });
})();
