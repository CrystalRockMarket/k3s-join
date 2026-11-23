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
      _hp: form._hp.value,
      reference: form.dataset.reference
    };
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
      return fetch("/api/v1/forms/" + SOURCE, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Kituwa-Form-Token": token
        },
        body: JSON.stringify(payload())
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

  fetchToken().catch(function () {
    say("The form could not start (no token). Reload the page once.", "bad");
    button.disabled = true;
  });
})();
