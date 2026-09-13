/* =====================================================================
   telegram_formatter — BYOB-Modul (eigene Bot-Session im Web)
   =====================================================================
   Bring Your Own Bot: Nutzer verbinden ihren eigenen Telegram-Bot in
   einer ephemeren Server-Session (RAM-only, TTL/Leerlauf-Timeout) und
   senden darüber privat — statt über den geteilten Bot, dessen Chat
   öffentlich einsehbar ist.

   Architektur-Vertrag (siehe docs/DECENTRAL_BOT_ARCHITECTURE.md, Modus B):
     * Der Session-Handle (opaker Zufallswert) lebt NUR im JS-Speicher —
       kein localStorage/sessionStorage, kein Cookie. Neuladen der Seite
       = clientseitig beendet; die Server-Session endet spätestens mit
       ihrem Timeout bzw. über /api/byob/close.
     * Das Token-Feld wird nach dem Session-Start geleert; der Token
       selbst wird genau einmal (Session-Start) übertragen.
     * Dieses Modul stellt `window.tfByob` bereit:
         { isActive(): bool,
           describeTarget(): {bot, chat} | null,
           sendText(text): Promise<{ok,status,data}> }
       app.js delegiert den Senden-Button daran (Fallback: geteilter Bot) und
       fragt describeTarget() für die Senden-Bestätigung ab (konkreter Bot +
       konkreter Ziel-Chat im Dialog).
     * DOM-Verträge (IDs) sind durch tests/test_frontend.py abgsichert;
       Funktionsabläufe tests/frontend/jsdom_spec.cjs.
   ===================================================================== */
(function () {
    "use strict";

    var form = document.getElementById("byobForm");
    // Ohne BYOB-Panel (Betreiber hat es deaktiviert) nichts tun.
    if (!form) {
        return;
    }

    var tokenInput = document.getElementById("byobToken");
    var chatInput = document.getElementById("byobChat");
    var consentBox = document.getElementById("byobConsent");
    var startBtn = document.getElementById("byobStartBtn");
    var errorBox = document.getElementById("byobError");
    var activePanel = document.getElementById("byobActive");
    var botName = document.getElementById("byobBotName");
    var chatLabel = document.getElementById("byobChatLabel");
    var countdown = document.getElementById("byobCountdown");
    var statsLabel = document.getElementById("byobStats");
    var closeBtn = document.getElementById("byobCloseBtn");
    var discoverBtn = document.getElementById("byobDiscoverBtn");
    var discoverResult = document.getElementById("byobDiscoverResult");
    var sendPathNote = document.getElementById("sendPathNote");

    var base = (document.body.dataset.byobBase || "/api/byob").replace(/\/$/, "");
    var TOKEN_RE = /^\d{5,16}:[A-Za-z0-9_-]{35}$/;
    var CHAT_RE = /^-?\d{1,32}$/;
    var STATUS_POLL_MS = 30000;

    // Session-Zustand (nur RAM — bewusst nicht persistiert).
    var sessionId = null;
    var sessionSecret = null;
    var limits = null;
    var ttlRemaining = 0;
    var idleRemaining = 0;
    var tickTimer = null;
    var pollTimer = null;
    // Identität der aktiven Session für die Senden-Bestätigung (app.js fragt
    // via tfByob.describeTarget() ab, bevor der Dialog gebaut wird).
    var sessionBot = null;
    var sessionChat = null;

    function setError(message, kind) {
        errorBox.textContent = message || "";
        errorBox.classList.toggle("is-error", kind === "error");
        errorBox.classList.toggle("is-ok", kind === "ok");
    }

    function postJson(path, body) {
        return fetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        }).then(function (res) {
            return res.json().then(function (data) {
                return { ok: res.ok, status: res.status, data: data };
            }, function () {
                return { ok: res.ok, status: res.status, data: {} };
            });
        });
    }

    function fmtMinutes(seconds) {
        if (seconds <= 0) {
            return "abgelaufen";
        }
        var m = Math.floor(seconds / 60);
        var s = Math.floor(seconds % 60);
        return m + ":" + (s < 10 ? "0" : "") + s + " Min.";
    }

    function updateSendPath() {
        if (!sendPathNote) {
            return;
        }
        if (sessionId) {
            sendPathNote.innerHTML = "";
            sendPathNote.textContent =
                "✓ Versand über deine eigene Bot-Session — privat (geteilter Bot wird nicht genutzt).";
            sendPathNote.classList.add("is-private");
        } else {
            sendPathNote.classList.remove("is-private");
            // Ursprungstext (Warnung/Platzhalter) wiederherstellen: das
            // Template liefert ihn serverseitig; ein Neuladen wäre übertrieben.
            sendPathNote.hidden = false;
            if (!sendPathNote.dataset.defaultHtml) {
                sendPathNote.dataset.defaultHtml = sendPathNote.innerHTML;
            } else {
                sendPathNote.innerHTML = sendPathNote.dataset.defaultHtml;
            }
        }
        window.tfSendLabel = sessionId ? "Über eigenen Bot senden" : undefined;
        var label = document.getElementById("sendBtnLabel");
        if (label && !document.getElementById("sendBtn").disabled) {
            label.textContent = sessionId
                ? "Über eigenen Bot senden"
                : "An Telegram senden";
        }
    }

    function renderSessionStatus() {
        var remaining = Math.min(ttlRemaining, idleRemaining);
        countdown.textContent =
            "Session läuft noch " + fmtMinutes(remaining) +
            " (endet automatisch — spätestens nach " +
            fmtMinutes(limits ? limits.ttl_seconds : 1800) +
            " bzw. nach " + fmtMinutes(limits ? limits.idle_timeout_seconds : 600) +
            " ohne Versand).";
        countdown.classList.toggle("is-warn", remaining <= 60);
    }

    function startTimers() {
        stopTimers();
        tickTimer = window.setInterval(function () {
            if (ttlRemaining > 0) { ttlRemaining -= 1; }
            if (idleRemaining > 0) { idleRemaining -= 1; }
            renderSessionStatus();
        }, 1000);
        pollTimer = window.setInterval(pollStatus, STATUS_POLL_MS);
    }

    function stopTimers() {
        if (tickTimer) { window.clearInterval(tickTimer); tickTimer = null; }
        if (pollTimer) { window.clearInterval(pollTimer); pollTimer = null; }
    }

    function showActive(on) {
        activePanel.hidden = !on;
        form.hidden = on;
        if (on) {
            form.setAttribute("aria-hidden", "true");
        } else {
            form.removeAttribute("aria-hidden");
        }
    }

    function activateSession(data) {
        sessionId = data.session_id;
        sessionSecret = data.session_secret;
        limits = data.limits || null;
        ttlRemaining = limits ? limits.ttl_seconds : 1800;
        idleRemaining = limits ? limits.idle_timeout_seconds : 600;
        var bot = data.bot || {};
        botName.textContent = bot.handle || bot.display_name || ("Bot " + (bot.id || "?"));
        chatLabel.textContent = data.chat_id || "?";
        sessionBot = bot.handle || bot.display_name || ("Bot " + (bot.id || "?"));
        sessionChat = data.chat_id || "?";
        setError("");
        showActive(true);
        renderSessionStatus();
        startTimers();
        updateSendPath();
        // Sicherheit: Token-Feld leeren — es wird nicht mehr gebraucht.
        tokenInput.value = "";
    }

    function deactivateSession(message, kind) {
        var had = sessionId !== null;
        sessionId = null;
        sessionSecret = null;
        limits = null;
        sessionBot = null;
        sessionChat = null;
        stopTimers();
        showActive(false);
        updateSendPath();
        if (had) {
            setError(message || "Session beendet — Token wurde verworfen.", kind || "ok");
        }
    }

    function pollStatus() {
        if (!sessionId) {
            return Promise.resolve();
        }
        return postJson(base + "/status", {
            session_id: sessionId,
            session_secret: sessionSecret
        }).then(function (res) {
            var data = res.data || {};
            if (!data.active) {
                deactivateSession("Session abgelaufen (Timeout) — bitte neu öffnen.", "error");
                return;
            }
            ttlRemaining = data.ttl_remaining_seconds || 0;
            idleRemaining = data.idle_remaining_seconds || 0;
            if (statsLabel && typeof data.messages_sent === "number") {
                statsLabel.textContent =
                    data.messages_sent + " Nachricht(en), " +
                    (data.chunks_sent || 0) + " Teil(e) gesendet";
            }
            renderSessionStatus();
        }).catch(function () { /* Netzwerkfehler: nächster Poll versucht es erneut */ });
    }

    function openSession(event) {
        event.preventDefault();
        setError("");
        var token = (tokenInput.value || "").trim();
        var chat = (chatInput.value || "").trim();

        if (!TOKEN_RE.test(token)) {
            setError("Token-Format ungültig. Erwartet: Bot-ID:Doppelpunkt:35 Zeichen " +
                     "(von @BotFather, ohne Leerzeichen/Anführungszeichen).", "error");
            tokenInput.focus();
            return;
        }
        if (!CHAT_RE.test(chat)) {
            setError("Chat-ID fehlt oder ist ungültig (Ganzzahl, z. B. 4711 oder -1001234567890). " +
                     "Tipp: „Chat-ID erkennen“ probieren.", "error");
            chatInput.focus();
            return;
        }
        if (!consentBox.checked) {
            setError("Bitte bestätige den Hinweis zum Umgang mit dem Token (Checkbox).", "error");
            consentBox.focus();
            return;
        }

        startBtn.disabled = true;
        postJson(base + "/session", {
            token: token,
            chat_id: chat,
            consent: true
        }).then(function (res) {
            startBtn.disabled = false;
            if (res.ok && res.data.session_id) {
                activateSession(res.data);
            } else {
                setError(res.data.error || "Session konnte nicht geöffnet werden.", "error");
            }
        }).catch(function () {
            startBtn.disabled = false;
            setError("Netzwerkfehler — Session konnte nicht geöffnet werden.", "error");
        });
    }

    function closeSession() {
        if (!sessionId) {
            return;
        }
        var sid = sessionId;
        postJson(base + "/close", {
            session_id: sid,
            session_secret: sessionSecret
        }).then(function () {
            /* Auch bei Fehler: clientseitig beenden — die Server-Session
               endet spätestens mit ihrem Timeout. */
            deactivateSession();
        }).catch(function () {
            deactivateSession();
        });
    }

    function discoverChats() {
        setError("");
        discoverResult.textContent = "";
        var token = (tokenInput.value || "").trim();
        if (!TOKEN_RE.test(token)) {
            setError("Bitte zuerst einen gültigen Bot-Token eintragen, dann „Chat-ID erkennen“.", "error");
            tokenInput.focus();
            return;
        }
        discoverBtn.disabled = true;
        discoverResult.textContent = "Suche Chats — sende deinem Bot jetzt eine kurze Nachricht …";
        postJson(base + "/discover", { token: token }).then(function (res) {
            discoverBtn.disabled = false;
            discoverResult.textContent = "";
            var chats = (res.data && res.data.chats) || [];
            if (!res.ok) {
                setError(res.data.error || "Chat-Erkennung fehlgeschlagen.", "error");
                return;
            }
            if (!chats.length) {
                setError("Keine Nachrichten gefunden. Sende deinem Bot in Telegram eine " +
                         "Nachricht (z. B. /start) und klicke erneut auf „Chat-ID erkennen“.", "error");
                return;
            }
            chats.forEach(function (chat) {
                var chip = document.createElement("button");
                chip.type = "button";
                chip.className = "tf-chip";
                chip.title = "Übernimmt diese Chat-ID";
                var idSpan = document.createElement("span");
                idSpan.textContent = String(chat.id);
                var metaSpan = document.createElement("span");
                metaSpan.className = "tf-chip__meta";
                metaSpan.textContent = (chat.name || "?") + " · " + (chat.type || "?");
                chip.appendChild(idSpan);
                chip.appendChild(metaSpan);
                chip.addEventListener("click", function () {
                    chatInput.value = String(chat.id);
                    discoverResult.textContent = "";
                    chatInput.focus();
                });
                discoverResult.appendChild(chip);
            });
        }).catch(function () {
            discoverBtn.disabled = false;
            discoverResult.textContent = "";
            setError("Netzwerkfehler bei der Chat-Erkennung.", "error");
        });
    }

    // Öffentlicher Vertrag für app.js (Senden-Button-Routing + Bestätigungsdialog).
    window.tfByob = {
        isActive: function () {
            return sessionId !== null;
        },
        // Ziel der aktiven Session für die Senden-Bestätigung: konkreter Bot
        // + konkreter Chat — oder null, wenn keine Session läuft.
        describeTarget: function () {
            return sessionId
                ? { bot: sessionBot, chat: sessionChat }
                : null;
        },
        sendText: function (text) {
            var sid = sessionId;
            return postJson(base + "/send", {
                session_id: sid,
                session_secret: sessionSecret,
                text: text
            })
                .then(function (res) {
                    if (res.status === 410 || res.status === 404) {
                        deactivateSession(
                            "Eigene Session abgelaufen — bitte neu öffnen (Token erneut eintragen).",
                            "error");
                    } else if (res.ok && res.data && res.data.session) {
                        ttlRemaining = res.data.session.ttl_remaining_seconds || ttlRemaining;
                        idleRemaining = res.data.session.idle_remaining_seconds || idleRemaining;
                        renderSessionStatus();
                    }
                    return res;
                });
        }
    };

    form.addEventListener("submit", openSession);
    closeBtn.addEventListener("click", closeSession);
    discoverBtn.addEventListener("click", discoverChats);
    updateSendPath();
})();
