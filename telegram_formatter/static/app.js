/* telegram_formatter — Editor-UI (Audit H-5: ausgelagert aus Inline-<script>,
   damit eine CSP ohne 'unsafe-inline' tragfaehig ist; Audit O-5: Debounce). */
(function () {
    "use strict";

    var input = document.getElementById("input");
    var preview = document.getElementById("preview");
    var payloads = document.getElementById("payloads");
    var sendBtn = document.getElementById("sendBtn");
    var resetBtn = document.getElementById("resetBtn");
    var sendStatus = document.getElementById("sendStatus");
    var convertUrl = document.body.dataset.convertUrl || "api/convert";
    var sendUrl = document.body.dataset.sendUrl || "api/send";

    var CONVERT_DEBOUNCE_MS = 300;
    var convertTimer = null;

    // Einfache Markdown-Vorschau (nur zur Orientierung im Editor).
    function renderPreview() {
        var t = input.value || "";
        t = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        t = t.replace(/^#+\s+(.*)$/gm, "<b>$1</b>");
        t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
        t = t.replace(/\*(.+?)\*/g, "<i>$1</i>");
        t = t.replace(/__(.+?)__/g, "<u>$1</u>");
        t = t.replace(/\n/g, "<br>");
        preview.innerHTML = t || "Vorschau erscheint hier…";
    }

    function postJson(url, body) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        }).then(function (res) {
            return res.json().then(function (data) {
                return { ok: res.ok, status: res.status, data: data };
            }, function () {
                return { ok: res.ok, status: res.status, data: {} };
            });
        });
    }

    function refreshPayloads() {
        if (!input.value.trim()) {
            // Leerer Editor erzeugt keinen Roundtrip (und keinen POST beim
            // Seitenaufruf).
            payloads.textContent = "—";
            sendStatus.textContent = "";
            return Promise.resolve();
        }
        return postJson(convertUrl, { text: input.value }).then(function (res) {
            var data = res.data || {};
            if (!res.ok && data.error) {
                payloads.textContent = "Fehler: " + data.error;
                sendStatus.textContent = "";
                return;
            }
            payloads.textContent = JSON.stringify(data, null, 2);
            if (data.count > 1) {
                sendStatus.textContent =
                    "Hinweis: " + data.count + " Nachrichten (automatisch aufgeteilt).";
            } else {
                sendStatus.textContent = "";
            }
        });
    }

    function scheduleRefresh() {
        clearTimeout(convertTimer);
        convertTimer = setTimeout(refreshPayloads, CONVERT_DEBOUNCE_MS);
    }

    // Setzt die komplette Arbeitsflaeche zurueck: Eingabe, Vorschau,
    // Payloads, Statusmeldung — und gibt den Fokus ans Eingabefeld zurueck.
    function resetAll() {
        clearTimeout(convertTimer);
        input.value = "";
        preview.innerHTML = "Vorschau erscheint hier…";
        payloads.textContent = "—";
        sendStatus.textContent = "";
        input.focus();
    }

    function setBusy(busy) {
        sendBtn.disabled = busy;
        if (busy) {
            sendBtn.textContent = "Sende…";
        } else {
            sendBtn.innerHTML =
                '<i class="fa-solid fa-paper-plane mr-1" aria-hidden="true"></i> An Telegram senden';
        }
    }

    function send() {
        setBusy(true);
        postJson(sendUrl, { text: input.value }).then(function (res) {
            var data = res.data || {};
            if (data.error) {
                var extra = "";
                if (typeof data.retry_after === "number") {
                    extra = " Warte " + Math.ceil(data.retry_after) + " s.";
                }
                if (data.sent_before_error > 0) {
                    extra += " Bereits gesendet: " + data.sent_before_error + " Teil(en) — nicht komplett wiederholen.";
                }
                sendStatus.textContent = "Fehler: " + data.error + extra;
            } else {
                sendStatus.textContent = "✅ " + (data.sent || 0) + " Nachricht(en) gesendet.";
            }
            setBusy(false);
        }).catch(function () {
            sendStatus.textContent = "Netzwerkfehler.";
            setBusy(false);
        });
    }

    input.addEventListener("input", function () {
        renderPreview();
        scheduleRefresh();
    });
    sendBtn.addEventListener("click", send);
    resetBtn.addEventListener("click", resetAll);

    renderPreview();
})();
