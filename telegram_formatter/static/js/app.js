/* =====================================================================
   telegram_formatter — Editor-UI (Konvertierung, Vorschau, Versand)
   =====================================================================
   Audit H-5: ausgelagert aus Inline-<script>, damit die CSP ohne
   'unsafe-inline' auskommt. Audit O-5: Debounce auf /api/convert.
   UI-Regeln seit dem Redesign (siehe docs/DESIGN.md):
     * Keine Farbwerte hier — Theme-Färbung ist allein Sache der CSS-Tokens.
     * Keine class-Namen erfinden: gesetzt werden nur die Zustandsklassen
       is-ok / is-error / is-over / is-private (in components.css definiert).
     * DOM-Verträge (IDs) sind durch tests/test_frontend.py abgesichert.
   *
   * Versand-Routing — **ein** Zustand, zwei Wege (seit v2.6.0):
   * `activePath()` liefert den wirksamen Weg ("own" | "shared" | null).
   *     own     — aktive BYOB-Session aus static/js/byob.js (window.tfByob)
   *     shared  — geteilter Bot der Instanz über /api/send, freigeschaltet
   *               durch TELEGRAM_FORMATTER_SHARED_WEB_SEND bei gepinntem
   *               Zielchat (data-shared-send="1" am <body>)
   * null bedeutet: kein Weg verfügbar → Senden wird abgewiesen, die UI ver-
   * weist auf BYOB bzw. den Operator-Token.
   * app.js ist der *einzige* Besitzer von Versandweg-Anzeige (#sendPathNote),
   * Button-Beschriftung (#sendBtnLabel) und Bestätigungsdialog; byob.js mel-
   * det Session-Wechsel über das Event `tf:botsessionchange` am document.
   *
   * Sende-Bestätigung (seit v2.3.0, Versandweg-Wahl seit v2.6.0): Ein Klick
   * auf „An Telegram senden“ (oder Strg/Cmd+Enter) öffnet ZUERST den Bestäti-
   * gungs-Dialog #sendConfirm. Er zeigt den gewählten Versandweg als Radio-
   * felder (eigener Bot = privat, geteilter Bot @mdtotxt_bot = öffentlich),
   * den konkreten Absender-Bot, das konkrete Ziel und eine Nachrichten-
   * vorschau. Erst „Jetzt senden“ (#sendConfirmOk) ruft send() auf; „Ab-
   * brechen“ (#sendConfirmCancel), Escape und ein Klick auf den Backdrop
   * schließen den Dialog, ohne etwas zu versenden. Der Fokus bleibt dabei
   * im Dialog (Tab-Falle) und kehrt danach zum Auslöser zurück.
   * Anonyme Shared-Sendungen bestätigen die öffentliche Sichtbarkeit noch
   * einmal im Request (`confirm_public: true`) — der Server verlangt das Feld
   * (siehe `telegram_formatter/app.py::_public_consent`), die Bestätigung
   * passiert also nachweisbar im Dialog und nicht stillschweigend.
   *
   * Kanal-Offenlegung (seit v2.9.0): Der geteilte Weg sendet in genau einen
   * öffentlichen Kanal. Dieses Skript nennt ihn deshalb überall dort, wo es
   * den Versandweg beschreibt — Hinweis unter dem Button, Radio-Zeile und
   * „Ziel" im Dialog, eigene Kanal-Zeile mit klickbarem Link plus Löschfrist
   * (#sendConfirmChannelRow) und Erfolgsstatus nach dem Senden. Die Werte
   * kommen ausschließlich aus den <body>-Attributen `data-shared-chat-url`,
   * `data-shared-chat-label` und `data-shared-retention-text`, die der Server
   * aus EINER Quelle baut (`app.py::_shared_channel`) — hier wird kein Kanal-
   * name und keine Frist erfunden. Ist kein Link konfiguriert, bleibt die
   * Kanal-Zeile ausgeblendet (kein toter Link), der Text nennt dann den Bot.
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
    var sendPathNote = document.getElementById("sendPathNote");
    var convertUrl = document.body.dataset.convertUrl || "/api/convert";
    var sendUrl = document.body.dataset.sendUrl || "/api/send";

    // Sende-Bestätigung (Modal): welcher Weg, welcher Bot, welches Ziel.
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
    var sendConfirmPaths = document.getElementById("sendConfirmPaths");
    var sendConfirmPathOwnRow = document.getElementById("sendConfirmPathOwnRow");
    var sendConfirmPathSharedRow = document.getElementById("sendConfirmPathSharedRow");
    var sendConfirmPathOwn = document.getElementById("sendConfirmPathOwn");
    var sendConfirmPathShared = document.getElementById("sendConfirmPathShared");
    var sendConfirmPathOwnTitle = document.getElementById("sendConfirmPathOwnTitle");
    var sendConfirmPathOwnMeta = document.getElementById("sendConfirmPathOwnMeta");
    var sendConfirmPathSharedTitle = document.getElementById("sendConfirmPathSharedTitle");
    var sendConfirmPathSharedMeta = document.getElementById("sendConfirmPathSharedMeta");
    // Kanal-Zeile im Dialog: Link + Aufbewahrungsdauer des geteilten Ziels.
    var sendConfirmChannelRow = document.getElementById("sendConfirmChannelRow");
    var sendConfirmChannelLink = document.getElementById("sendConfirmChannelLink");
    var sendConfirmChannelNote = document.getElementById("sendConfirmChannelNote");

    // Zustand der Instanz — kommt serverseitig in <body data-…> an:
    //   data-shared-send           geteilter Bot darf vom Browser genutzt werden
    //   data-shared-configured     es existiert überhaupt ein geteilter Bot
    //   data-shared-bot            Anzeige-Handle dieses Bots
    //   data-shared-chat-url       öffentlicher Link zum Ziel-Kanal (leer = keiner)
    //   data-shared-chat-label     Kurzform des Kanals (t.me/<handle>)
    //   data-shared-retention-text fertiger Satz zur automatischen Löschung
    // Die Werte stammen aus EINER Quelle (app.py::_shared_channel) — dieses
    // Skript erfindet weder Kanalnamen noch Aufbewahrungsdauern.
    var sharedBotHandle = document.body.dataset.sharedBot || "geteilter Bot";
    var sharedChatUrl = document.body.dataset.sharedChatUrl || "";
    var sharedChatLabel = document.body.dataset.sharedChatLabel || "";
    var sharedRetentionText = document.body.dataset.sharedRetentionText || "";
    var sharedSendAvailable = document.body.dataset.sharedSend === "1";
    var sharedBotConfigured = document.body.dataset.sharedConfigured === "1";
    var byobEnabled = document.body.dataset.byobEnabled !== "0";

    var PATH_OWN = "own";
    var PATH_SHARED = "shared";

    var CONVERT_DEBOUNCE_MS = 300;
    var CONFIRM_PREVIEW_CHARS = 280;
    var convertTimer = null;
    var PLACEHOLDER = "Vorschau erscheint hier…";
    var REGULAR_LIMIT = 4096;
    var RICH_LIMIT = 32768;
    var INPUT_LIMIT = 64000;
    var lastFocused = null;
    // Zuletzt im Dialog gewählter Weg. Bewusst nur RAM (kein localStorage):
    // die Wahl ist eine Sitzungs-Präferenz, kein Persistenzversprechen.
    var chosenPath = null;

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
        // NUL-Zeichen entfernen (Parität zu utils.normalize_text, Audit N-1/R-4):
        // die Vorschau nutzt \u0000 als Platzhalter-Marker — Nutzer-NULs würden
        // den Restore-Mechanismus kollidieren lassen.
        raw = raw.replace(/\u0000/g, "");
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

        /* 1) fenced code blocks ```lang\n...\\n``` */
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
        charCount.classList.toggle("is-over", n > INPUT_LIMIT);
        if (n > INPUT_LIMIT) {
            charCount.title = "Eingabe zu lang — maximal 64000 Zeichen (wird beim Senden abgewiesen). Aktuell " +
                n.toLocaleString("de-DE") + " Zeichen.";
        } else if (n > REGULAR_LIMIT) {
            charCount.title = "Länger als 4096 Zeichen — wird automatisch in mehrere Nachrichten aufgeteilt " +
                "(Rich bis 32768, insgesamt bis 64000 — Codeblöcke bleiben je Nachricht wohlgeformt)." ;
        } else {
            charCount.title = "Telegram-Limit: 4096 Zeichen klassisch / 32768 Rich — Eingaben bis 64000 werden sinnvoll aufgeteilt." ;
        }
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

    /* ---------------------------------------------------------------
       Versandweg: ein Zustand, drei mögliche Ausgänge
       --------------------------------------------------------------- */
    function byobActive() {
        return !!(window.tfByob && window.tfByob.isActive());
    }

    function availablePaths() {
        var paths = [];
        if (byobActive()) {
            paths.push(PATH_OWN);
        }
        if (sharedSendAvailable) {
            paths.push(PATH_SHARED);
        }
        return paths;
    }

    /**
     * Wirksamer Weg: die Nutzerwahl, solange sie noch verfügbar ist — sonst
     * der erste verfügbare. Die Reihenfolge in `availablePaths()` ist die
     * Privatsphäre-Reihenfolge: eigene Session vor geteiltem Bot.
     */
    function activePath() {
        var paths = availablePaths();
        if (chosenPath && paths.indexOf(chosenPath) !== -1) {
            return chosenPath;
        }
        return paths.length ? paths[0] : null;
    }

    /** Bot + Ziel des Weges — für Button-Beschriftung, Hinweis und Dialog. */
    function describePath(path) {
        if (path === PATH_OWN) {
            var own = (window.tfByob && window.tfByob.describeTarget
                && window.tfByob.describeTarget()) || {};
            return {
                key: PATH_OWN,
                bot: own.bot || "dein eigener Bot",
                chat: String(own.chat || "?"),
                isOwn: true,
                summary: "nur dein Ziel-Chat (privat)",
            };
        }
        if (path === PATH_SHARED) {
            return {
                key: PATH_SHARED,
                bot: sharedBotHandle,
                chat: sharedChatLabel || null,
                chatUrl: sharedChatUrl,
                retention: sharedRetentionText,
                isOwn: false,
                summary: sharedChatLabel
                    ? "öffentlicher Kanal " + sharedChatLabel
                    : "öffentlicher, gemeinsamer Chat dieser Seite",
            };
        }
        return { key: null, bot: "", chat: null, chatUrl: "", retention: "", isOwn: false, summary: "" };
    }

    /* -----------------------------------------------------------------
       Ziel-Benennung des geteilten Wegs.
       Einzige Quelle ist `data-shared-chat-label` (app.py::_shared_channel);
       dieses Skript erfindet keinen Kanalnamen. Ist keiner konfiguriert,
       wechseln beide Fassungen auf die allgemeine Formulierung — die Sätze
       bleiben damit in beiden Fällen grammatikalisch ganz (dieselbe Regel wie
       das Template-Makro `channel_noun()`).
       ----------------------------------------------------------------- */
    /** Satzbaustein im Akkusativ: „… sendet in <Phrase>." */
    function sharedTargetNoun() {
        return sharedChatLabel
            ? "den öffentlichen Kanal " + sharedChatLabel
            : "einen öffentlichen, gemeinsamen Chat";
    }

    /** Überschriftenform für Faktenzeilen: „Öffentlicher Kanal t.me/…". */
    function sharedTargetHeading() {
        return sharedChatLabel
            ? "Öffentlicher Kanal " + sharedChatLabel
            : "Öffentlicher, gemeinsamer Chat dieser Seite";
    }

    /** Hinweis, wenn kein Weg offen ist — abhängig davon, ob BYOB bereitsteht. */
    function noPathHint() {
        if (!byobEnabled) {
            return "Kein Versandweg verfügbar: eigener Bot ist auf dieser Instanz " +
                "deaktiviert, der geteilte Bot nur per API erreichbar.";
        }
        return sharedBotConfigured
            ? "Geteilter Versand ist hier API-only — starte eine BYOB-Session " +
              "(oder Betreiber: TELEGRAM_FORMATTER_SHARED_WEB_SEND=1)."
            : "Kein authentifizierter Versandweg aktiv — starte eine BYOB-Session.";
    }

    function sendButtonLabel() {
        var path = activePath();
        if (path === PATH_OWN) {
            return "Über eigenen Bot senden";
        }
        if (path === PATH_SHARED) {
            return "An Telegram senden (" + sharedBotHandle + ")";
        }
        return "An Telegram senden";
    }

    function updateSendButtonLabel() {
        if (sendBtnLabel && !sendBtn.disabled) {
            sendBtnLabel.textContent = sendButtonLabel();
        }
    }

    // Hinweistext unter dem Button — immer konsistent zum wirksamen Weg.
    function updateSendPathNote() {
        if (!sendPathNote) {
            return;
        }
        var path = activePath();
        if (path === PATH_OWN) {
            sendPathNote.classList.add("is-private");
            sendPathNote.textContent =
                "✓ Versand über deine eigene Bot-Session " + describePath(PATH_OWN).bot +
                " — privat (geteilter Bot wird nicht genutzt).";
        } else if (path === PATH_SHARED) {
            sendPathNote.classList.remove("is-private");
            sendPathNote.textContent =
                "⚠ Versand über den geteilten Bot " + sharedBotHandle + " in " +
                sharedTargetNoun() + " — öffentlich sichtbar für alle!" +
                (sharedRetentionText ? " " + sharedRetentionText : "") +
                " Für private Inhalte: eigene Bot-Session starten " +
                "(Abschnitt „Eigener Bot — BYOB“ unten).";
        } else {
            sendPathNote.classList.remove("is-private");
            sendPathNote.textContent = sharedBotConfigured
                ? "Geteilter Bot " + sharedBotHandle +
                  (sharedChatLabel ? " (Kanal " + sharedChatLabel + ")" : "") +
                  " ist nur per API erreichbar — " +
                  (byobEnabled
                      ? "senden über eine eigene Bot-Session (BYOB, Abschnitt unten)."
                      : "BYOB ist auf dieser Instanz deaktiviert.")
                : "Kein geteilter Bot konfiguriert — " + (byobEnabled
                      ? "unten eine eigene Bot-Session (BYOB) starten, um zu senden."
                      : "BYOB ist auf dieser Instanz deaktiviert.");
        }
    }

    /** Alles, was vom wirksamen Weg abhängt (Label, Hinweis, Dialog-Inhalt). */
    function refreshSendPath() {
        updateSendButtonLabel();
        updateSendPathNote();
        if (sendConfirm && !sendConfirm.hidden) {
            renderSendConfirm();
        }
    }

    function setBusy(busy) {
        sendBtn.disabled = busy;
        if (sendBtnLabel) {
            sendBtnLabel.textContent = busy ? "Sende…" : sendButtonLabel();
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
            setSendStatus("✅ " + (data.sent || 0) + " Nachricht(en)" +
                sentViaText(data.via) + " gesendet.", "ok");
        }
        setBusy(false);
    }

    /**
     * Erfolgsmeldung nach dem Versand: Beim geteilten Weg nennt sie Bot **und**
     * Kanal, damit auch nach dem Klicken sichtbar bleibt, wo die Nachricht
     * liegt (der Dialog ist da schon zu). Die Kanal-Angabe kommt aus dem
     * `via`-Feld der API und fällt auf die Werte aus dem <body> zurück.
     */
    function sentViaText(via) {
        if (!via || !via.public) {
            return "";
        }
        var bot = via.bot || sharedBotHandle;
        var label = via.chat_url
            ? String(via.chat_url).replace(/^https?:\/\//, "")
            : sharedChatLabel;
        return " über " + bot + (label ? " in den öffentlichen Kanal " + label : "");
    }

    function send() {
        var path = activePath();
        if (!path) {
            setSendStatus(noPathHint(), "error");
            return;
        }
        setBusy(true);
        // Der Dialog liegt hinter diesem Aufruf: „öffentlich senden“ wurde dort
        // bestätigt, deshalb darf confirm_public mit (der Server verlangt das
        // Feld für anonyme Shared-Sendungen — siehe app.py::_public_consent).
        var request = path === PATH_OWN
            ? window.tfByob.sendText(input.value)
            : postJson(sendUrl, { text: input.value, confirm_public: true });
        request.then(handleSendResponse).catch(function () {
            setSendStatus("Netzwerkfehler.", "error");
            setBusy(false);
        });
    }

    /* ---------------------------------------------------------------
       Sende-Bestätigung: Weg wählen, Fakten prüfen, abbrechen können.
       --------------------------------------------------------------- */
    function renderPathChoices(paths, selected) {
        if (!sendConfirmPaths || !sendConfirmPathOwnRow || !sendConfirmPathSharedRow) {
            return;  // Dialog-Markup fehlt zur Hälfte: Fakten bauen wir trotzdem.
        }
        var ownAvailable = paths.indexOf(PATH_OWN) !== -1;
        var sharedAvailable = paths.indexOf(PATH_SHARED) !== -1;

        sendConfirmPaths.hidden = !ownAvailable && !sharedAvailable;
        sendConfirmPathOwnRow.hidden = !ownAvailable;
        sendConfirmPathSharedRow.hidden = !sharedAvailable;
        // Zustandsklasse für die CSS-Markierung (is-selected in components.css).
        sendConfirmPathOwnRow.classList.toggle("is-selected", selected === PATH_OWN);
        sendConfirmPathSharedRow.classList.toggle("is-selected", selected === PATH_SHARED);

        if (ownAvailable) {
            var own = describePath(PATH_OWN);
            sendConfirmPathOwnTitle.textContent = own.bot + " — dein eigener Bot";
            sendConfirmPathOwnMeta.textContent = "privat · Chat " + own.chat;
            sendConfirmPathOwn.checked = selected === PATH_OWN;
        }
        if (sharedAvailable) {
            sendConfirmPathSharedTitle.textContent = sharedBotHandle + " — geteilter Bot dieser Seite";
            sendConfirmPathSharedMeta.textContent =
                "öffentlich · " + (sharedChatLabel ? "Kanal " + sharedChatLabel : "gemeinsamer Chat") +
                " · alle Besucher sehen die Nachricht";
            sendConfirmPathShared.checked = selected === PATH_SHARED;
        }
    }

    /**
     * Kanal-Zeile im Dialog: beim geteilten Weg der klickbare Link zum
     * öffentlichen Kanal plus Aufbewahrungsdauer, sonst ausgeblendet.
     *
     * `href` wird nur gesetzt, wenn der Server einen geprüften Link geliefert
     * hat (app.py::normalize_public_chat_url akzeptiert ausschließlich
     * https://t.me/<handle>) — ohne Wert bleibt die Zeile unsichtbar, statt
     * einen toten „#“-Link anzubieten.
     */
    function renderChannelFact(info) {
        if (!sendConfirmChannelRow) {
            return;
        }
        var show = Boolean(info && info.chatUrl);
        sendConfirmChannelRow.hidden = !show;
        if (!show) {
            return;
        }
        if (sendConfirmChannelLink) {
            sendConfirmChannelLink.href = info.chatUrl;
            sendConfirmChannelLink.textContent = info.chat;
        }
        if (sendConfirmChannelNote) {
            sendConfirmChannelNote.textContent = info.retention || "";
        }
    }

    /** Füllt den Dialog (Wege + Fakten + Hinweise) aus dem gewählten Weg. */
    function renderSendConfirm() {
        var paths = availablePaths();
        var path = activePath();
        var info = describePath(path);
        renderPathChoices(paths, path);

        if (path === PATH_OWN) {
            sendConfirmBot.textContent = info.bot + " (dein eigener Bot)";
            sendConfirmTarget.textContent = "Chat " + info.chat + " — " + info.summary;
            sendConfirmOkLabel.textContent = "Über " + info.bot + " senden";
        } else if (path === PATH_SHARED) {
            sendConfirmBot.textContent = info.bot + " (geteilter Bot dieser Seite)";
            sendConfirmTarget.textContent = sharedTargetHeading() +
                " — sichtbar für alle Besucher";
            sendConfirmOkLabel.textContent = sharedChatLabel
                ? "In den Kanal " + sharedChatLabel + " senden"
                : "Öffentlich senden";
        } else {
            sendConfirmBot.textContent = "— (kein Versandweg verfügbar)";
            sendConfirmTarget.textContent = noPathHint();
            sendConfirmOkLabel.textContent = "Senden nicht möglich";
        }
        renderChannelFact(path === PATH_SHARED ? info : null);

        var raw = input.value || "";
        sendConfirmPreview.textContent = raw.length > CONFIRM_PREVIEW_CHARS
            ? raw.slice(0, CONFIRM_PREVIEW_CHARS) + " …"
            : raw;
        sendConfirmLength.textContent =
            raw.length.toLocaleString("de-DE") + " Zeichen";

        // Genau einer der drei Hinweise passt zum gewählten Weg.
        sendConfirmWarning.hidden = path !== PATH_SHARED;
        sendConfirmPrivate.hidden = path !== PATH_OWN;
        sendConfirmUnavailable.hidden = path !== null;
        sendConfirmOk.disabled = path === null;
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

        lastFocused = document.activeElement;
        renderSendConfirm();
        sendConfirm.hidden = false;
        // Fokus auf Abbrechen: die Voreinstellung „eigener Bot“ soll nicht
        // durch einen versehentlichen Enter-Druck bestätigt werden.
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
        // Deaktivierte Schaltflächen überspringen (Tab springt darüber) — sonst
        // hängt die Falle am ausgegrauten „Senden nicht möglich“-Knopf.
        var focusables = sendConfirm.querySelectorAll(
            "button:not([disabled]), [href], input:not([disabled]), textarea, select, " +
            "[tabindex]:not([tabindex=\"-1\"])"
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
            if (sendConfirmOk.disabled) {
                return;
            }
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
        // Versandweg umschalten — Fakten, Hinweise und Beschriftung ziehen mit.
        [sendConfirmPathOwn, sendConfirmPathShared].forEach(function (radio) {
            if (!radio) {
                return;
            }
            radio.addEventListener("change", function () {
                // refreshSendPath() baut Label, Hinweis und (weil offen) auch den
                // Dialog neu — eine Funktion, ein Zustand, keine Doppelautoren.
                if (radio.checked) {
                    chosenPath = radio.value;
                    refreshSendPath();
                }
            });
        });
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

    // byob.js meldet Session-Öffnung/-Ende: Label, Hinweis und ein offener
    // Dialog müssen den neuen Versandweg spiegeln.
    document.addEventListener("tf:botsessionchange", refreshSendPath);

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
    refreshSendPath();
})();
