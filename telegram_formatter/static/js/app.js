/* =====================================================================
   telegram_formatter — Editor-UI (Konvertierung, Vorschau, Versand)
   =====================================================================
   Audit H-5: ausgelagert aus Inline-<script>, damit die CSP ohne
   'unsafe-inline' auskommt. Audit O-5: Debounce auf /api/convert.
   UI-Regeln seit dem Redesign (siehe docs/DESIGN.md):
     * Keine Farbwerte hier — Theme-Färbung ist allein Sache der CSS-Tokens.
     * Keine class-Namen erfinden: gesetzt werden nur die Zustandsklassen
       is-ok / is-error / is-over (in components.css definiert).
     * DOM-Verträge (IDs) sind durch tests/test_frontend.py abgsichert.
   *
   * Versand-Routing (seit v2.2.0): Ist eine BYOB-Session aktiv, stellt
   * static/js/byob.js das Objekt `window.tfByob` bereit — send() delegiert
   * dann an `tfByob.sendText()` (eigener Bot, privat). Ohne Session gilt
   * der klassische Weg über /api/send (geteilter Bot). Das Label des
   * Senden-Buttons liest setBusy() aus `window.tfSendLabel` (falls byob.js
   * es gesetzt hat), der Status-Text läuft wie immer über #sendStatus.
   *
   * Sende-Bestätigung (seit v2.3.0): Ein Klick auf „An Telegram senden“
   * (oder Strg/Cmd+Enter) öffnet ZUERST den Bestätigungs-Dialog
   * #sendConfirm. Er zeigt den konkreten Absender-Bot, das konkrete Ziel
   * und eine Nachrichtenvorschau — befüllt aus `window.tfByob
   * .describeTarget()` bzw. den <body>-Datatributen data-shared-bot und
   * data-configured. Erst „Jetzt senden“ (#sendConfirmOk) ruft send()
   * auf; „Abbrechen“ (#sendConfirmCancel), Escape und ein Klick auf den
   * Backdrop schließen den Dialog, ohne etwas zu versenden. Der Fokus
   * bleibt dabei im Dialog (Tab-Falle) und kehrt danach zum Auslöser
   * zurück.
   ===================================================================== */
(function () {
    "use strict";

    var input = document.getElementById("input");
    var preview = document.getElementById("preview");
    var payloads = document.getElementById("payloads");
    var sendBtn = document.getElementById("sendBtn");
    var sendBtnLabel = document.getElementById("sendBtnLabel");
    var resetBtn = document.getElementById("resetBtn");
    var sendStatus = document.getElementById("sendStatus");
    var charCount = document.getElementById("charCount");
    var convertUrl = document.body.dataset.convertUrl || "api/convert";
    var sendUrl = document.body.dataset.sendUrl || "api/send";

    // Sende-Bestätigung (Modal): welcher Bot, welches Ziel — abbrechen möglich.
    var sendConfirm = document.getElementById("sendConfirm");
    var sendConfirmBackdrop = document.getElementById("sendConfirmBackdrop");
    var sendConfirmBot = document.getElementById("sendConfirmBot");
    var sendConfirmTarget = document.getElementById("sendConfirmTarget");
    var sendConfirmPreview = document.getElementById("sendConfirmPreview");
    var sendConfirmLength = document.getElementById("sendConfirmLength");
    var sendConfirmWarning = document.getElementById("sendConfirmWarning");
    var sendConfirmPrivate = document.getElementById("sendConfirmPrivate");
    var sendConfirmUnavailable = document.getElementById("sendConfirmUnavailable");
    var sendConfirmOk = document.getElementById("sendConfirmOk");
    var sendConfirmOkLabel = document.getElementById("sendConfirmOkLabel");
    var sendConfirmCancel = document.getElementById("sendConfirmCancel");
    var sharedBotHandle = document.body.dataset.sharedBot || "geteilter Bot";
    var sharedConfigured = document.body.dataset.configured === "1";
    var lastFocused = null;

    var CONVERT_DEBOUNCE_MS = 300;
    var CONFIRM_PREVIEW_CHARS = 280;
    var convertTimer = null;
    var PLACEHOLDER = "Vorschau erscheint hier…";
    var REGULAR_LIMIT = 4096;

    function escapeHtml(t) {
        return t
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    /* Einzeilige, irreversible Hilfskonstruktion: geschützte Abschnitte
       (Code, Formeln) werden vor allen anderen Ersetzungen extrahiert und
       am Ende wieder eingesetzt — so zerlegt Markdown-Hervorhebung kein
       \`code\` und kein $x_1$. */
    function renderPreview() {
        var raw = input.value || "";
        if (!raw.trim()) {
            preview.innerHTML = PLACEHOLDER;
            return;
        }
        var store = [];
        function protect(html) {
            store.push(html);
            return "\u0000" + (store.length - 1) + "\u0000";
        }

        var t = escapeHtml(raw);

        /* 1) fenced code blocks ```lang\n...\n``` */
        t = t.replace(/```[^\n]*\n([\s\S]*?)```/g, function (_m, code) {
            return protect("<pre class=\"tf-preview-code\"><code>" + code + "</code></pre>");
        });
        /* 2) Inline-Code `...` */
        t = t.replace(/`([^`\n]+)`/g, function (_m, code) {
            return protect("<code>" + code + "</code>");
        });
        /* 3) LaTeX: $$...$$ (Display) und $...$ (inline) — nur als Hinweis
           formatiert; echtes Rendering übernimmt Telegram (Rich Message). */
        t = t.replace(/\$\$([^$\n]+)\$\$/g, function (_m, math) {
            return protect("<span class=\"tf-preview-math\">" + math + "</span>");
        });
        t = t.replace(/\$([^$\n]+)\$/g, function (_m, math) {
            return protect("<span class=\"tf-preview-math\">" + math + "</span>");
        });

        /* 4) Telegram-Markdown */
        t = t.replace(/^#{1,6}\s+(.*)$/gm, "<b>$1</b>");
        t = t.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
        t = t.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<i>$2</i>");
        t = t.replace(/(^|[^_\w])__([^_\n]+)__(?![^_\w])/g, "$1<u>$2</u>");
        t = t.replace(/(^|[^_\w])_([^_\n]+)_(?![^_\w])/g, "$1<i>$2</i>");
        t = t.replace(/~~([^~\n]+)~~/g, "<s>$1</s>");
        t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
            "<a href=\"$2\" target=\"_blank\" rel=\"noopener\">$1</a>");
        t = t.replace(/^&gt;\s?(.*)$/gm, "<i>$1</i>");

        /* 5) Zeilenumbrüche wiederherstellen, Schutzbelegungen auflösen */
        t = t.replace(/\n/g, "<br>");
        t = t.replace(/\u0000(\d+)\u0000/g, function (_m, i) {
            return store[Number(i)];
        });
        preview.innerHTML = t;
    }

    function updateCharCount() {
        if (!charCount) {
            return;
        }
        var n = input.value.length;
        charCount.textContent = n.toLocaleString("de-DE") + " Zeichen";
        charCount.classList.toggle("is-over", n > REGULAR_LIMIT);
        charCount.title = n > REGULAR_LIMIT
            ? "Länger als 4096 Zeichen — wird automatisch in mehrere Nachrichten aufgeteilt (bzw. als Rich Message bis 32768 gesendet)."
            : "Telegram-Limit: 4096 Zeichen pro klassischer Nachricht, 32768 pro Rich Message.";
    }

    function setSendStatus(text, kind) {
        sendStatus.textContent = text;
        sendStatus.classList.toggle("is-ok", kind === "ok");
        sendStatus.classList.toggle("is-error", kind === "error");
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
            setSendStatus("", null);
            return Promise.resolve();
        }
        return postJson(convertUrl, { text: input.value }).then(function (res) {
            var data = res.data || {};
            if (!res.ok && data.error) {
                payloads.textContent = "Fehler: " + data.error;
                setSendStatus("", null);
                return;
            }
            payloads.textContent = JSON.stringify(data, null, 2);
            if (data.count > 1) {
                setSendStatus(
                    "Hinweis: " + data.count + " Nachrichten (automatisch aufgeteilt).",
                    null
                );
            } else {
                setSendStatus("", null);
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
        preview.innerHTML = PLACEHOLDER;
        payloads.textContent = "—";
        setSendStatus("", null);
        updateCharCount();
        input.focus();
    }

    function setBusy(busy) {
        sendBtn.disabled = busy;
        if (sendBtnLabel) {
            sendBtnLabel.textContent = busy
                ? "Sende…"
                : (window.tfSendLabel || "An Telegram senden");
        }
    }

    function handleSendResponse(res) {
        var data = res.data || {};
        if (data.error) {
            var extra = "";
            if (typeof data.retry_after === "number") {
                extra = " Warte " + Math.ceil(data.retry_after) + " s.";
            }
            if (data.sent_before_error > 0) {
                extra += " Bereits gesendet: " + data.sent_before_error + " Teil(en) — nicht komplett wiederholen.";
            }
            setSendStatus("Fehler: " + data.error + extra, "error");
        } else {
            setSendStatus("✅ " + (data.sent || 0) + " Nachricht(en) gesendet.", "ok");
        }
        setBusy(false);
    }

    function send() {
        setBusy(true);
        // BYOB aktiv? Dann versendet byob.js über die eigene Session —
        // Antwortform ist identisch (ok/status/data), die Auswertung bleibt
        // hier zentral.
        var request = (window.tfByob && window.tfByob.isActive())
            ? window.tfByob.sendText(input.value)
            : postJson(sendUrl, { text: input.value });
        request.then(handleSendResponse).catch(function () {
            setSendStatus("Netzwerkfehler.", "error");
            setBusy(false);
        });
    }

    /* ---------------------------------------------------------------
       Sende-Bestätigung: Vor jedem Versand wird der konkrete Weg
       angezeigt (geteilter Bot = öffentlich vs. eigene Session =
       privat). Erst „Jetzt senden“ versendet — Abbrechen, Escape und
       Klick auf den Backdrop senden nichts.
       --------------------------------------------------------------- */
    function sendTargetInfo() {
        if (window.tfByob && window.tfByob.isActive()) {
            var own = (window.tfByob.describeTarget && window.tfByob.describeTarget()) || {};
            return {
                own: true,
                bot: own.bot || "dein eigener Bot",
                chat: String(own.chat || "?")
            };
        }
        return { own: false, bot: sharedBotHandle, chat: null };
    }

    function openSendConfirm() {
        if (!input.value.trim()) {
            setSendStatus("Nichts zu senden — der Editor ist leer.", null);
            input.focus();
            return;
        }
        if (!sendConfirm) {
            // Defensiver Fallback (Markup fehlt): direkt senden wie früher.
            send();
            return;
        }

        var target = sendTargetInfo();
        lastFocused = document.activeElement;

        if (target.own) {
            sendConfirmBot.textContent = target.bot + " (dein eigener Bot)";
            sendConfirmTarget.textContent =
                "Chat " + target.chat + " — nur dein Ziel-Chat (privat)";
            sendConfirmOkLabel.textContent = "Über " + target.bot + " senden";
        } else if (sharedConfigured) {
            sendConfirmBot.textContent = target.bot + " (geteilter Bot dieser Seite)";
            sendConfirmTarget.textContent =
                "Gemeinsamer Chat dieser Seite — öffentlich sichtbar für alle Besucher";
            sendConfirmOkLabel.textContent = "Über " + target.bot + " senden";
        } else {
            sendConfirmBot.textContent = "— (kein geteilter Bot konfiguriert)";
            sendConfirmTarget.textContent =
                "Kein Versandweg aktiv — eigene Bot-Session (BYOB) starten";
            sendConfirmOkLabel.textContent = "Senden versuchen";
        }

        var raw = input.value || "";
        sendConfirmPreview.textContent = raw.length > CONFIRM_PREVIEW_CHARS
            ? raw.slice(0, CONFIRM_PREVIEW_CHARS) + " …"
            : raw;
        sendConfirmLength.textContent =
            raw.length.toLocaleString("de-DE") + " Zeichen";

        sendConfirmWarning.hidden = !(!target.own && sharedConfigured);
        sendConfirmPrivate.hidden = !target.own;
        sendConfirmUnavailable.hidden = !(!target.own && !sharedConfigured);

        sendConfirm.hidden = false;
        sendConfirmCancel.focus();
    }

    function closeSendConfirm(restoreFocus) {
        if (!sendConfirm || sendConfirm.hidden) {
            return;
        }
        sendConfirm.hidden = true;
        if (restoreFocus) {
            var back = lastFocused;
            lastFocused = null;
            if (back && typeof back.focus === "function" &&
                document.contains(back) && !back.disabled) {
                back.focus();
            } else {
                input.focus();
            }
        } else {
            lastFocused = null;
        }
    }

    /* Tab-Falle: Fokus bleibt im offenen Dialog (nicht dahinter). */
    function trapFocus(event) {
        var focusables = sendConfirm.querySelectorAll(
            "button, [href], input, textarea, select, [tabindex]:not([tabindex=\"-1\"])"
        );
        if (!focusables.length) {
            return;
        }
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        var active = document.activeElement;
        if (event.shiftKey && (active === first || !sendConfirm.contains(active))) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && (active === last || !sendConfirm.contains(active))) {
            event.preventDefault();
            first.focus();
        }
    }

    if (sendConfirm) {
        sendConfirmOk.addEventListener("click", function () {
            closeSendConfirm(false);
            send();
        });
        sendConfirmCancel.addEventListener("click", function () {
            closeSendConfirm(true);
        });
        if (sendConfirmBackdrop) {
            sendConfirmBackdrop.addEventListener("click", function () {
                closeSendConfirm(true);
            });
        }
        document.addEventListener("keydown", function (event) {
            if (sendConfirm.hidden) {
                return;
            }
            if (event.key === "Escape") {
                event.preventDefault();
                closeSendConfirm(true);
            } else if (event.key === "Tab") {
                trapFocus(event);
            }
        });
    }

    input.addEventListener("input", function () {
        renderPreview();
        updateCharCount();
        scheduleRefresh();
    });
    sendBtn.addEventListener("click", openSendConfirm);
    resetBtn.addEventListener("click", resetAll);

    /* Komfort: Strg/Cmd+Enter öffnet die Senden-Bestätigung aus dem Editor. */
    input.addEventListener("keydown", function (event) {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            if (!sendBtn.disabled) {
                openSendConfirm();
            }
        }
    });

    renderPreview();
    updateCharCount();
})();
