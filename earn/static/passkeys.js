/* Passkey ceremonies. External file so the CSP never needs unsafe-inline. */
(function () {
  "use strict";
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.getAttribute("content") : "";
  var status = document.getElementById("passkey-status");

  function say(text) {
    if (status) { status.textContent = text; }
  }

  function b64urlToBuf(value) {
    var pad = value.length % 4 === 0 ? "" : "====".slice(value.length % 4);
    var base64 = (value + pad).replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(base64);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) { bytes[i] = raw.charCodeAt(i); }
    return bytes.buffer;
  }

  function bufToB64url(buffer) {
    var bytes = new Uint8Array(buffer);
    var raw = "";
    for (var i = 0; i < bytes.length; i += 1) { raw += String.fromCharCode(bytes[i]); }
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) {
          throw new Error((data && data.error) || "Request failed.");
        }
        return data;
      });
    });
  }

  function prepareCreate(options) {
    options.challenge = b64urlToBuf(options.challenge);
    options.user.id = b64urlToBuf(options.user.id);
    (options.excludeCredentials || []).forEach(function (item) {
      item.id = b64urlToBuf(item.id);
    });
    return options;
  }

  function prepareGet(options) {
    options.challenge = b64urlToBuf(options.challenge);
    (options.allowCredentials || []).forEach(function (item) {
      item.id = b64urlToBuf(item.id);
    });
    return options;
  }

  function createPayload(credential) {
    var response = credential.response;
    var transports = [];
    if (typeof response.getTransports === "function") {
      try { transports = response.getTransports(); } catch (e) { transports = []; }
    }
    return {
      id: credential.id,
      rawId: bufToB64url(credential.rawId),
      type: credential.type,
      clientExtensionResults: credential.getClientExtensionResults(),
      response: {
        clientDataJSON: bufToB64url(response.clientDataJSON),
        attestationObject: bufToB64url(response.attestationObject),
        transports: transports
      }
    };
  }

  function getPayload(credential) {
    var response = credential.response;
    return {
      id: credential.id,
      rawId: bufToB64url(credential.rawId),
      type: credential.type,
      clientExtensionResults: credential.getClientExtensionResults(),
      response: {
        clientDataJSON: bufToB64url(response.clientDataJSON),
        authenticatorData: bufToB64url(response.authenticatorData),
        signature: bufToB64url(response.signature),
        userHandle: response.userHandle ? bufToB64url(response.userHandle) : null
      }
    };
  }

  var loginButton = document.getElementById("passkey-login");
  if (loginButton) {
    loginButton.addEventListener("click", function () {
      if (!window.PublicKeyCredential) { say("This browser does not support passkeys."); return; }
      loginButton.disabled = true;
      say("Waiting for your authenticator…");
      var flow = "";
      post("/account/passkey/login/begin").then(function (data) {
        flow = data.flow;
        return navigator.credentials.get({ publicKey: prepareGet(data.options) });
      }).then(function (credential) {
        return post("/account/passkey/login/finish", {
          flow: flow, credential: getPayload(credential)
        });
      }).then(function () {
        window.location.assign(loginButton.getAttribute("data-next") || "/earn/");
      }).catch(function (error) {
        say(error.message || "The passkey sign-in did not complete.");
        loginButton.disabled = false;
      });
    });
  }

  var addButton = document.getElementById("passkey-add");
  if (addButton) {
    addButton.addEventListener("click", function () {
      if (!window.PublicKeyCredential) { say("This browser does not support passkeys."); return; }
      addButton.disabled = true;
      say("Waiting for your authenticator…");
      var flow = "";
      post("/account/passkey/register/begin").then(function (data) {
        flow = data.flow;
        return navigator.credentials.create({ publicKey: prepareCreate(data.options) });
      }).then(function (credential) {
        return post("/account/passkey/register/finish", {
          flow: flow, credential: createPayload(credential), label: "Passkey"
        });
      }).then(function () {
        say("Passkey added. Reloading…");
        window.setTimeout(function () { window.location.reload(); }, 600);
      }).catch(function (error) {
        say(error.message || "The passkey could not be added.");
        addButton.disabled = false;
      });
    });
  }
})();
