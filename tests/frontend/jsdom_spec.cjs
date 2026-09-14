/* =====================================================================
   telegram_formatter — funktionale Frontend-Smoke-Tests (jsdom)
   =====================================================================
   Ausgeführt von tests/test_jsdom_smoke.py (pytest skippt sauber, wenn
   Node/jsdom fehlen). Aufruf:  node jsdom_spec.cjs <gerendertes-index.html>

   Diese Spec prüft das Zusammenspiel von theme.js, app.js und byob.js im
   echten DOM: Theme-Boot aus localStorage, Klicks auf den Switcher,
   Live-Vorschau, Debounce+Fetch gegen /api/convert, Versandweg-Wahl
   (geteilter Bot vs. eigene Session), Senden, Zurücksetzen, Fehlerpfad.
   jsdom emuliert Layout nicht (kein Computed-Styling), dafür aber DOM-APIs,
   Events, Timers und localStorage — genau die Schicht, die unsere Logik nutzt.
   ===================================================================== */
"use strict";

const fs = require("fs");
const path = require("path");

let JSDOM, VirtualConsole;
try {
    ({ JSDOM, VirtualConsole } = require("jsdom"));
} catch (err) {
    console.log("MISSING_JSDOM");
    process.exit(77); // => pytest.skip
}

const htmlPath = process.argv[2];
if (!htmlPath || !fs.existsSync(htmlPath)) {
    console.error("usage: node jsdom_spec.cjs <rendered-index.html>");
    process.exit(2);
}

const STATIC_DIR = path.resolve(__dirname, "../../telegram_formatter/static");
const read = (p) => fs.readFileSync(p, "utf-8");

/**
 * Harness aus dem gerenderten Template. `flags` überschreibt die <body>-Attribute,
 * um Zustände zu prüfen, die der Server je nach Konfiguration rendert (die
 * *Renderings* selbst testet tests/test_frontend.py — hier geht es um das
 * Verhalten der Skripte bei gegebenem Markup).
 */
function buildHarnessHtml(flags = {}) {
    let html = read(htmlPath);
    for (const [name, value] of Object.entries(flags)) {
        // `[^"]*` statt `[01]`: außer den Flags (shared-send/byob-enabled)
        // werden auch Textattribute überschrieben (shared-chat-url/-label,
        // shared-retention-text), um die degradierte Konfiguration zu testen.
        html = html.replace(new RegExp(`data-${name}="[^"]*"`), `data-${name}="${value}"`);
    }
    // CSS-Links sind für jsdom bedeutungslos, Skript-/Linktags stören beim
    // Nachladen (kein Server) -> entfernen und die echten Dateien inline
    // in den Harness einsetzen (Skriptreihenfolge wie im Template).
    html = html.replace(/<link[^>]*>/g, "");
    html = html.replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    html = html.replace(
        "</head>",
        `<script>${read(path.join(STATIC_DIR, "js/theme.js"))}</script></head>`
    );
    html = html.replace(
        "</body>",
        `<script>${read(path.join(STATIC_DIR, "js/app.js"))}</script>` +
        `<script>${read(path.join(STATIC_DIR, "js/byob.js"))}</script></body>`
    );
    return html;
}

/* ------------------------------------------------------------------ */
let failures = 0;
function check(name, condition, detail = "") {
    if (condition) {
        console.log(`  ok  ${name}`);
    } else {
        failures += 1;
        console.log(`FAIL  ${name} ${detail}`);
    }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* jsdom meldet ungefangene Exceptions aus Skripten über die VirtualConsole —
   sammeln und am Ende als eigenen Test ausweisen. */
function trackedConsole(store) {
    const vc = new VirtualConsole();
    vc.on("jsdomError", (err) => store.push(String((err && err.message) || err)));
    return vc;
}

async function main() {
    const harness = buildHarnessHtml();
    const calls = [];
    let convertPayload = { count: 1, messages: [{ kind: "regular", payload: { chat_id: "1" } }] };
    let sendResult = {
        ok: true,
        // Der Shared-Endpunkt meldet seit 2.6.0 `via` — die UI zeigt daraus
        // an, *worum* gesendet wurde (Bot + Öffentlichkeit).
        body: {
            sent: 2,
            results: [{ kind: "regular", status: "ok" }],
            via: { bot: "@mdtotxt_bot", chat_id: "-100999", public: true },
        },
    };

    const scriptErrors = [];

    /* Fall 1: Theme "dark" ist gespeichert -> Boot MUSS dunkel starten. */
    let dom = new JSDOM(harness, {
        runScripts: "dangerously",
        virtualConsole: trackedConsole(scriptErrors),
        url: "https://formatter.local/",
        beforeParse(window) {
            window.localStorage.setItem("tf-theme", "dark");
            window.matchMedia = (query) => ({
                matches: false,
                media: query,
                addEventListener() {},
                removeEventListener() {},
                addListener() {},
                removeListener() {},
            });
            window.fetch = (url, opts) => {
                calls.push({ url: String(url), body: opts && opts.body });
                const result = String(url).includes("api/send") ? sendResult : { ok: true, body: convertPayload };
                return Promise.resolve({
                    ok: result.ok,
                    status: result.ok ? 200 : 429,
                    json: () => Promise.resolve(result.body),
                });
            };
        },
    });
    let { window } = dom;
    let doc = window.document;
    await new Promise((resolve) => window.addEventListener("load", resolve));

    check("Theme-Boot: gespeichertes 'dark' wird vor/nach Parse gesetzt",
        doc.documentElement.getAttribute("data-theme") === "dark",
        `ist ${doc.documentElement.getAttribute("data-theme")}`);
    check("Theme-Boot: meta theme-color auf Dark umgestellt",
        doc.querySelector('meta[name="theme-color"]').getAttribute("content") === "#0e1621");

    await sleep(60); // DOMContentLoaded -> init() des Switchers
    check("Switcher: aktiver Button = gespeicherte Wahl (aria-pressed)",
        doc.querySelector('[data-theme-choice="dark"]').getAttribute("aria-pressed") === "true");
    check("Switcher: aktive Klasse is-active gesetzt",
        doc.querySelector('[data-theme-choice="dark"]').classList.contains("is-active"));

    /* Fall 2: Klick auf "colorful" -> Attribut + Persistenz + State */
    doc.querySelector('[data-theme-choice="colorful"]').click();
    check("Klick 'colorful': data-theme wechselt",
        doc.documentElement.getAttribute("data-theme") === "colorful");
    check("Klick 'colorful': in localStorage gespeichert",
        window.localStorage.getItem("tf-theme") === "colorful");
    check("Klick 'colorful': is-active wandert mit",
        doc.querySelector('[data-theme-choice="colorful"]').classList.contains("is-active") &&
        !doc.querySelector('[data-theme-choice="dark"]').classList.contains("is-active"));

    /* Fall 3: Kein JS-Theme -> Systemeinstellung (hier: hell) */
    doc.querySelector('[data-theme-choice="auto"]').click();
    check("Klick 'auto': Attribut wird 'auto' (CSS-Media übernimmt)",
        doc.documentElement.getAttribute("data-theme") === "auto");
    check("Klick 'auto': localStorage folgt",
        window.localStorage.getItem("tf-theme") === "auto");

    /* ---------------- Editor: Vorschau, Payloads, Senden, Reset ---------- */
    const input = doc.getElementById("input");
    const preview = doc.getElementById("preview");
    const payloads = doc.getElementById("payloads");
    const sendStatus = doc.getElementById("sendStatus");

    check("Startzustand: Platzhalter in der Vorschau",
        preview.textContent.includes("Vorschau erscheint hier…"));
    check("Startzustand: Payloads zeigen '—'", payloads.textContent.trim() === "—");

    input.value = "**fett** *schief* `code` ~~strich~~ und $x^2$";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    const pv = preview.innerHTML;
    check("Vorschau: <b> für **fett**", pv.includes("<b>fett</b>"));
    check("Vorschau: <i> für *schief*", pv.includes("<i>schief</i>"));
    check("Vorschau: <code> für `code`", pv.includes("<code>code</code>"));
    check("Vorschau: <s> für ~~strich~~", pv.includes("<s>strich</s>"));
    check("Vorschau: Formel-Highlight tf-preview-math", pv.includes("tf-preview-math") && pv.includes("x^2"));
    check("Zeichenzähler aktualisiert (45 Zeichen)",
        doc.getElementById("charCount").textContent.includes("45 Zeichen"),
        doc.getElementById("charCount").textContent);

    await sleep(420); // Debounce 300 ms + Ticks
    check("Debounce: genau ein /api/convert-POST nach Tipppause",
        calls.filter((c) => c.url.includes("api/convert")).length === 1,
        `calls=${JSON.stringify(calls)}`);
    check("Payloads-Panel zeigt Server-JSON", payloads.textContent.includes('"count": 1'));

    /* Leerer Editor => kein Roundtrip */
    input.value = "";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    await sleep(420);
    check("Leerer Editor löst keinen zusätzlichen POST aus",
        calls.filter((c) => c.url.includes("api/convert")).length === 1);
    check("Leerer Editor setzt Payloads auf '—'", payloads.textContent.trim() === "—");

    /* Aufteilungs-Hinweis */
    convertPayload = {
        count: 3,
        messages: [
            { kind: "regular", payload: { text: "a" } },
            { kind: "regular", payload: { text: "b" } },
            { kind: "rich", payload: { rich_message: { markdown: "c" } } },
        ],
    };
    input.value = "**viel** text";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    await sleep(420);
    check("Splitting-Hinweis erscheint bei count>1",
        sendStatus.textContent.includes("3 Nachrichten (automatisch aufgeteilt)"),
        sendStatus.textContent);

    /* Senden — seit v2.3.0 mit Bestätigungsdialog, seit v2.6.0 mit
       Versandweg-Auswahl. Dieser Harness ist die gehostete Demo: geteilter Bot
       konfiguriert + Browser-Versand freigeschaltet, keine BYOB-Session.
       Erwartung: @mdtotxt_bot ist der angebotene (und vorausgewählte) Weg. */
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("Dialog: öffnet sich beim Klick auf Senden",
        doc.getElementById("sendConfirm").hidden === false);
    check("Dialog: Versandweg-Auswahl ist sichtbar und bietet nur den geteilten Bot",
        doc.getElementById("sendConfirmPaths").hidden === false &&
        doc.getElementById("sendConfirmPathSharedRow").hidden === false &&
        doc.getElementById("sendConfirmPathOwnRow").hidden === true,
        doc.getElementById("sendConfirmPaths").outerHTML.slice(0, 80));
    check("Dialog: geteilter Bot ist vorausgewählt",
        doc.getElementById("sendConfirmPathShared").checked === true);
    check("Dialog: nennt @mdtotxt_bot als Absender und den öffentlichen Kanal als Ziel",
        doc.getElementById("sendConfirmBot").textContent.includes("@mdtotxt_bot") &&
        doc.getElementById("sendConfirmBot").textContent.includes("geteilter Bot") &&
        /öffentlich/i.test(doc.getElementById("sendConfirmTarget").textContent) &&
        doc.getElementById("sendConfirmTarget").textContent.includes("t.me/mdtotxt_bot_web"),
        doc.getElementById("sendConfirmBot").textContent + " / " +
        doc.getElementById("sendConfirmTarget").textContent);
    check("Dialog: Kanal-Zeile zeigt den klickbaren Link zum Ziel-Kanal",
        doc.getElementById("sendConfirmChannelRow").hidden === false &&
        doc.getElementById("sendConfirmChannelLink").getAttribute("href") ===
            "https://t.me/mdtotxt_bot_web" &&
        doc.getElementById("sendConfirmChannelLink").textContent === "t.me/mdtotxt_bot_web",
        doc.getElementById("sendConfirmChannelLink").outerHTML);
    check("Dialog: Kanal-Zeile nennt die automatische Löschung (30 Tage)",
        /automatisch nach 30 Tagen gelöscht/.test(
            doc.getElementById("sendConfirmChannelNote").textContent),
        doc.getElementById("sendConfirmChannelNote").textContent);
    check("Dialog: öffentliche Warnung ist eingeblendet, Privat-/Blocker-Hinweis nicht",
        doc.getElementById("sendConfirmWarning").hidden === false &&
        doc.getElementById("sendConfirmPrivate").hidden === true &&
        doc.getElementById("sendConfirmUnavailable").hidden === true);
    check("Dialog: Nachrichtenvorschau + Zeichenzahl",
        doc.getElementById("sendConfirmPreview").textContent.includes("**viel** text") &&
        doc.getElementById("sendConfirmLength").textContent.includes("Zeichen"));
    check("Button: Beschriftung nennt den geteilten Bot",
        doc.getElementById("sendBtnLabel").textContent.includes("@mdtotxt_bot"),
        doc.getElementById("sendBtnLabel").textContent);

    /* Abbrechen: Dialog zu, kein POST, Fokus zurück */
    doc.getElementById("sendConfirmCancel").click();
    check("Abbrechen: Dialog geschlossen", doc.getElementById("sendConfirm").hidden === true);
    check("Abbrechen: es ging nichts raus",
        !calls.some((c) => c.url.includes("api/send")),
        JSON.stringify(calls));

    /* Erneut öffnen und bestätigen — jetzt geht der Shared-POST wirklich raus. */
    doc.getElementById("sendBtn").click();
    await sleep(20);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    const sharedCall = calls.filter((c) => c.url.includes("api/send")).pop();
    check("Senden: Shared-POST läuft über /api/send", Boolean(sharedCall));
    check("Senden: öffentliche Sichtbarkeit wird bestätigt mitgeschickt (confirm_public)",
        Boolean(sharedCall) && JSON.parse(sharedCall.body).confirm_public === true,
        sharedCall && sharedCall.body);
    check("Senden: Erfolgsstatus nennt Bot und Ziel-Kanal",
        sendStatus.textContent.includes("2 Nachricht(en)") &&
        sendStatus.textContent.includes("@mdtotxt_bot") &&
        sendStatus.textContent.includes("t.me/mdtotxt_bot_web"),
        sendStatus.textContent);
    check("Senden: Button bleibt bedienbar", doc.getElementById("sendBtn").disabled === false);
    check("Senden: Statuszeile bekommt is-ok", sendStatus.classList.contains("is-ok"));

    /* Fehlerpfad (mit Dialog) */
    sendResult = { ok: false, body: { error: "Zu viele Sendeversuche", retry_after: 7, sent_before_error: 1 } };
    doc.getElementById("sendBtn").click();
    await sleep(20);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("Fehler: Retry-Hinweis und Teilfortschritt landen in der Statuszeile",
        sendStatus.textContent.includes("Warte 7 s") &&
        sendStatus.textContent.includes("Bereits gesendet: 1"),
        sendStatus.textContent);
    check("Fehler: Statuszeile bekommt is-error", sendStatus.classList.contains("is-error"));

    /* Escape schließt den Dialog wie Abbrechen */
    const sentBeforeEscape = calls.filter((c) => c.url.includes("api/send")).length;
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("Escape-Vorbereitung: Dialog offen", doc.getElementById("sendConfirm").hidden === false);
    doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    check("Escape: Dialog geschlossen ohne Senden",
        doc.getElementById("sendConfirm").hidden === true &&
        calls.filter((c) => c.url.includes("api/send")).length === sentBeforeEscape,
        `send-calls=${calls.filter((c) => c.url.includes("api/send")).length}`);


    /* Reset */
    doc.getElementById("resetBtn").click();
    check("Reset: Eingabe leer", input.value === "");
    check("Reset: Vorschau-Platzhalter zurück", preview.textContent.includes("Vorschau erscheint hier…"));
    check("Reset: Payloads '—'", payloads.textContent.trim() === "—");
    check("Reset: Statusmeldung weg", sendStatus.textContent === "");
    check("Reset: Zeichenzähler zurück auf 0", /^0 Zeichen/.test(doc.getElementById("charCount").textContent));

    /* Keine ungefangenen Fehler aus theme.js/app.js? */
    check("Keine ungefangenen Skriptfehler", scriptErrors.length === 0, scriptErrors.join(" / "));

    dom.window.close();

    /* Fall 4: frisches Dokument OHNE localStorage -> auto + media light. */
    dom = new JSDOM(buildHarnessHtml(), {
        runScripts: "dangerously",
        virtualConsole: trackedConsole(scriptErrors),
        url: "https://formatter.local/",
        beforeParse(window) {
            window.matchMedia = (query) => ({
                matches: /dark/.test(query), // System: dunkel!
                media: query,
                addEventListener() {},
                removeEventListener() {},
                addListener() {},
                removeListener() {},
            });
            window.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ count: 0, messages: [] }) });
        },
    });
    await new Promise((resolve) => dom.window.addEventListener("load", resolve));
    check("Ohne gespeicherte Wahl: data-theme='auto'",
        dom.window.document.documentElement.getAttribute("data-theme") === "auto");
    check("System dunkel + auto: meta theme-color auf Dark-Farbe",
        dom.window.document.querySelector('meta[name="theme-color"]').getAttribute("content") === "#0e1621");
    dom.window.close();

    check("Auch im zweiten Durchlauf keine Skriptfehler", scriptErrors.length === 0,
        scriptErrors.slice(-2).join(" / "));

    /* ------------------------------------------------------------------ */
    /* Fall 5: BYOB — eigene Bot-Session öffnen, senden, beenden           */
    /* ------------------------------------------------------------------ */
    let sessionState = "none"; // none | active | gone
    const byobSession = {
        session_id: "abcd1234efgh5678",
        bot: { id: 42, username: "mein_bot", display_name: "Mein Bot", handle: "@mein_bot" },
        chat_id: "-1001234567890",
        limits: { ttl_seconds: 1800, idle_timeout_seconds: 600, max_messages_per_minute: 20, max_input_chars: 100000 },
    };
    const byobCalls = [];
    const sharedWebCalls = []; // Body-Payloads der /api/send-Aufrufe in Fall 5
    dom = new JSDOM(buildHarnessHtml(), {
        runScripts: "dangerously",
        virtualConsole: trackedConsole(scriptErrors),
        url: "https://formatter.local/",
        beforeParse(window) {
            window.matchMedia = () => ({
                matches: false, media: "", addEventListener() {}, removeEventListener() {},
                addListener() {}, removeListener() {},
            });
            window.fetch = (url, opts) => {
                const u = String(url);
                const body = opts && opts.body ? String(opts.body) : "";
                byobCalls.push(u);
                let status = 200;
                let payload = { count: 1, messages: [] };
                if (u.includes("/api/byob/session")) {
                    status = 201; payload = byobSession;
                } else if (u.includes("/api/byob/discover")) {
                    payload = {
                        chats: [
                            { id: -1001234567890, type: "group", name: "Testgruppe" },
                            { id: 4711, type: "private", name: "Ich" },
                        ],
                    };
                } else if (u.includes("/api/byob/send")) {
                    if (sessionState === "active") {
                        payload = { sent: 2, session: { ttl_remaining_seconds: 1700, idle_remaining_seconds: 540 } };
                    } else {
                        status = 410; payload = { error: "Session abgelaufen oder unbekannt — bitte erneut öffnen." };
                    }
                } else if (u.includes("/api/byob/status")) {
                    payload = sessionState === "active"
                        ? { active: true, ttl_remaining_seconds: 1750, idle_remaining_seconds: 560, messages_sent: 1, chunks_sent: 2 }
                        : { active: false };
                } else if (u.includes("/api/byob/close")) {
                    sessionState = "gone"; payload = { closed: true };
                } else if (u.includes("/api/send")) {
                    sharedWebCalls.push(body);
                    payload = { sent: 1, via: { bot: "@mdtotxt_bot", chat_id: "-100999", public: true } };
                }
                return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(payload) });
            };
        },
    });
    window = dom.window;
    doc = window.document;
    await new Promise((resolve) => window.addEventListener("load", resolve));
    await sleep(60);

    check("BYOB: Panel sichtbar, Session-Bereich versteckt",
        !doc.getElementById("byobForm").hidden && doc.getElementById("byobActive").hidden);
    check("BYOB: Versandweg-Hinweis nennt den geteilten Bot (nicht die eigene Session)",
        /geteilt/.test(doc.getElementById("sendPathNote").textContent) &&
        !doc.getElementById("sendPathNote").classList.contains("is-private"),
        doc.getElementById("sendPathNote").textContent);
    check("BYOB: tfByob-Vertrag existiert und ist inaktiv",
        window.tfByob && window.tfByob.isActive() === false);

    /* Session öffnen: Validierungen zuerst */
    doc.getElementById("byobToken").value = "kaputtes-token";
    doc.getElementById("byobForm").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
    await sleep(40);
    check("BYOB: ungültiges Token wird clientseitig abgewiesen (kein POST)",
        !byobCalls.some((u) => u.includes("/api/byob/session")));
    check("BYOB: Fehlermeldung erscheint in #byobError",
        doc.getElementById("byobError").classList.contains("is-error"));

    /* Chat-Erkennung (vor dem Öffnen — das Token-Feld ist dann noch gefüllt):
       Chips rendern und Klick übernimmt die ID. */
    doc.getElementById("byobToken").value = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345";
    doc.getElementById("byobChat").value = "";
    doc.getElementById("byobDiscoverBtn").click();
    await sleep(40);
    const chips = doc.querySelectorAll("#byobDiscoverResult .tf-chip");
    check("BYOB: Chat-Erkennung rendert Chips", chips.length === 2, `chips=${chips.length}`);
    if (chips.length) {
        chips[0].click();
        check("BYOB: Chip-Klick übernimmt die Chat-ID",
            doc.getElementById("byobChat").value === "-1001234567890");
    }

    /* Erfolgreich öffnen */
    sessionState = "active";
    doc.getElementById("byobChat").value = "-1001234567890";
    doc.getElementById("byobConsent").checked = true;
    doc.getElementById("byobForm").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
    await sleep(60);
    check("BYOB: Session-Öffnung ging an /api/byob/session",
        byobCalls.some((u) => u.includes("/api/byob/session")));
    check("BYOB: aktive Session sichtbar, Formular versteckt",
        doc.getElementById("byobActive").hidden === false && doc.getElementById("byobForm").hidden === true);
    check("BYOB: Bot-Handle + Chat-ID in der Statusanzeige",
        doc.getElementById("byobBotName").textContent === "@mein_bot" &&
        doc.getElementById("byobChatLabel").textContent === "-1001234567890");
    check("BYOB: Token-Feld nach Start geleert",
        doc.getElementById("byobToken").value === "");
    check("BYOB: Versandweg-Hinweis zeigt privat",
        doc.getElementById("sendPathNote").classList.contains("is-private"));
    check("BYOB: Senden-Button-Label auf eigenen Bot",
        doc.getElementById("sendBtnLabel").textContent === "Über eigenen Bot senden");
    check("BYOB: Countdown läuft", /Session läuft noch/.test(doc.getElementById("byobCountdown").textContent));

    /* Senden über die Session — der Bestätigungsdialog muss den eigenen
       Bot und den Ziel-Chat nennen (nicht den geteilten Bot). */
    doc.getElementById("input").value = "**privat** via eigener Session";
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("BYOB-Dialog: eigener Bot als Absender",
        doc.getElementById("sendConfirmBot").textContent.includes("@mein_bot") &&
        doc.getElementById("sendConfirmBot").textContent.includes("eigener Bot"),
        doc.getElementById("sendConfirmBot").textContent);
    check("BYOB-Dialog: konkreter Ziel-Chat",
        doc.getElementById("sendConfirmTarget").textContent.includes("-1001234567890") &&
        doc.getElementById("sendConfirmTarget").textContent.includes("privat"),
        doc.getElementById("sendConfirmTarget").textContent);
    check("BYOB-Dialog: Privat-Hinweis statt öffentlicher Warnung",
        doc.getElementById("sendConfirmPrivate").hidden === false &&
        doc.getElementById("sendConfirmWarning").hidden === true);
    check("BYOB-Dialog: Kanal-Zeile bleibt beim eigenen Bot ausgeblendet",
        doc.getElementById("sendConfirmChannelRow").hidden === true);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    const sendCall = byobCalls.filter((u) => u.includes("/api/byob/send")).pop();
    check("BYOB: Senden geht an /api/byob/send", Boolean(sendCall));

    /*both Wege verfügbar: umschalten im Dialog — der gewählte Weg entscheidet,
       nicht „Session aktiv = Session zwingend“. */
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("BYOB-Dialog: beide Wege zur Wahl (eigener Bot vorausgewählt)",
        doc.getElementById("sendConfirmPathOwnRow").hidden === false &&
        doc.getElementById("sendConfirmPathSharedRow").hidden === false &&
        doc.getElementById("sendConfirmPathOwn").checked === true);
    doc.getElementById("sendConfirmPathShared").click();
    await sleep(20);
    check("BYOB-Dialog: Wahl auf geteilten Bot umgestellt — Fakten ziehen mit",
        doc.getElementById("sendConfirmPathShared").checked === true &&
        doc.getElementById("sendConfirmBot").textContent.includes("@mdtotxt_bot") &&
        doc.getElementById("sendConfirmWarning").hidden === false &&
        doc.getElementById("sendConfirmPrivate").hidden === true,
        doc.getElementById("sendConfirmBot").textContent);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("BYOB-Dialog: bewusst gewählter geteilter Bot nutzt /api/send",
        sharedWebCalls.length === 1 && JSON.parse(sharedWebCalls[0]).confirm_public === true,
        JSON.stringify(sharedWebCalls));
    check("BYOB-Dialog: Hinweis unter dem Button folgt der Wahl",
        doc.getElementById("sendPathNote").textContent.includes("@mdtotxt_bot") &&
        !doc.getElementById("sendPathNote").classList.contains("is-private"),
        doc.getElementById("sendPathNote").textContent);
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("BYOB-Dialog: die Wahl bleibt gemerkt und wird wieder angezeigt",
        doc.getElementById("sendConfirmPathShared").checked === true);
    doc.getElementById("sendConfirmPathOwn").click();
    await sleep(20);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("BYOB-Dialog: zurück auf den eigenen Bot — wieder /api/byob/send",
        byobCalls.filter((u) => u.includes("/api/byob/send")).length >= 2 &&
        sharedWebCalls.length === 1,
        `byob-send=${byobCalls.filter((u) => u.includes("/api/byob/send")).length} shared=${sharedWebCalls.length}`);
    check("BYOB: Erfolgsstatus erscheint",
        doc.getElementById("sendStatus").textContent.includes("2 Nachricht(en) gesendet"),
        doc.getElementById("sendStatus").textContent);

    /* Ablauf (410): UI muss auf das Formular zurückfallen */
    sessionState = "gone";
    doc.getElementById("sendBtn").click();
    await sleep(20);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("BYOB: 410 beim Senden beendet die Session clientseitig",
        window.tfByob.isActive() === false && doc.getElementById("byobForm").hidden === false);
    check("BYOB: Ablauf-Hinweis erscheint", doc.getElementById("byobError").classList.contains("is-error"));
    check("BYOB: Senden-Button-Label zurück auf den geteilten Bot",
        doc.getElementById("sendBtnLabel").textContent === "An Telegram senden (@mdtotxt_bot)",
        doc.getElementById("sendBtnLabel").textContent);
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("BYOB: Session weg ⇒ Weg-Auswahl bietet nur noch den geteilten Bot",
        doc.getElementById("sendConfirmPathOwnRow").hidden === true &&
        doc.getElementById("sendConfirmPathSharedRow").hidden === false &&
        doc.getElementById("sendConfirmPathShared").checked === true,
        doc.getElementById("sendConfirmPaths").outerHTML.slice(0, 120));
    doc.getElementById("sendConfirmCancel").click();

    dom.window.close();

    /* ------------------------------------------------------------------ */
    /* Fall 6: Regression — „Shared-Bot konfiguriert, aber API-only“        */
    /* ------------------------------------------------------------------ */
    /* Genau dieser Zustand war der Bug: geteilter Bot vorhanden, Browser-
       Versand aber deaktiviert (data-shared-send="0"). Die UI darf dann
       weder so tun, als gäbe es einen Versandweg, noch schweigen: sie zeigt
       den blockierten Weg samt dem Schalter, den der Betreiber setzen kann. */
    const apiOnlyCalls = [];
    dom = new JSDOM(buildHarnessHtml({ "shared-send": "0" }), {
        runScripts: "dangerously",
        virtualConsole: trackedConsole(scriptErrors),
        url: "https://formatter.local/",
        beforeParse(window) {
            window.matchMedia = () => ({
                matches: false, media: "", addEventListener() {}, removeEventListener() {},
                addListener() {}, removeListener() {},
            });
            window.fetch = (url, opts) => {
                const u = String(url);
                apiOnlyCalls.push(u);
                if (u.includes("/api/byob/session")) {
                    return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({
                        session_id: "sid-api-only",
                        session_secret: "secret-api-only",
                        bot: { id: 7, username: "privacy_bot", display_name: "Privacy", handle: "@privacy_bot" },
                        chat_id: "4711",
                        limits: { ttl_seconds: 1800, idle_timeout_seconds: 600 },
                    }) });
                }
                if (u.includes("/api/byob/send")) {
                    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
                        sent: 1, session: { ttl_remaining_seconds: 1700, idle_remaining_seconds: 500 },
                    }) });
                }
                return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ count: 1, messages: [] }) });
            };
        },
    });
    window = dom.window;
    doc = window.document;
    await new Promise((resolve) => window.addEventListener("load", resolve));
    await sleep(60);

    doc.getElementById("input").value = "Test ohne eigenen Bot";
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("API-only: kein Versandweg ⇒ Auswahl bleibt ausgeblendet",
        doc.getElementById("sendConfirmPaths").hidden === true);
    check("API-only: Blocker-Hinweis erklärt den Zustand und den Schalter",
        doc.getElementById("sendConfirmUnavailable").hidden === false &&
        doc.getElementById("sendConfirmBot").textContent.includes("kein Versandweg"),
        doc.getElementById("sendConfirmUnavailable").textContent);
    check("API-only: Senden-Button ist im Dialog deaktiviert",
        doc.getElementById("sendConfirmOk").disabled === true);
    check("API-only: Hinweistext unter dem Button bleibt ehrlich",
        doc.getElementById("sendPathNote").textContent.includes("nur per API"),
        doc.getElementById("sendPathNote").textContent);
    check("API-only: kein Versuch, /api/send zu treffen",
        !apiOnlyCalls.some((u) => u.includes("/api/send")),
        JSON.stringify(apiOnlyCalls));
    doc.getElementById("sendConfirmCancel").click();

    /* Mit eigener Session funktioniert der Versand auch in diesem Zustand. */
    doc.getElementById("byobToken").value = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345";
    doc.getElementById("byobChat").value = "4711";
    doc.getElementById("byobConsent").checked = true;
    doc.getElementById("byobForm").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
    await sleep(60);
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("API-only + Session: eigener Bot ist der angebotene Weg",
        doc.getElementById("sendConfirmPaths").hidden === false &&
        doc.getElementById("sendConfirmPathOwnRow").hidden === false &&
        doc.getElementById("sendConfirmPathSharedRow").hidden === true &&
        doc.getElementById("sendConfirmBot").textContent.includes("@privacy_bot"),
        doc.getElementById("sendConfirmBot").textContent);
    check("API-only + Session: Senden ist wieder möglich",
        doc.getElementById("sendConfirmOk").disabled === false);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("API-only + Session: Versand läuft über die eigene Session",
        apiOnlyCalls.some((u) => u.includes("/api/byob/send")) &&
        !apiOnlyCalls.some((u) => u.includes("/api/send")),
        JSON.stringify(apiOnlyCalls));
    dom.window.close();

    /* ------------------------------------------------------------------ */
    /* Fall 7: Regression — kein Kanal-Link konfiguriert                    */
    /* ------------------------------------------------------------------ */
    /* Der Betreiber hat TELEGRAM_FORMATTER_SHARED_CHAT_URL nicht (oder
       ungültig) gesetzt. Die UI darf dann weder einen Kanal *erfinden* noch
       einen toten Link anbieten — und die Sätze müssen grammatikalisch ganz
       bleiben („in einen öffentlichen, gemeinsamen Chat", nicht
       „in den öffentlichen Kanal "). */
    dom = new JSDOM(buildHarnessHtml({
        "shared-chat-url": "",
        "shared-chat-label": "",
        "shared-retention-text": "",
    }), {
        runScripts: "dangerously",
        virtualConsole: trackedConsole(scriptErrors),
        url: "https://formatter.local/",
        beforeParse(window) {
            window.matchMedia = () => ({
                matches: false, media: "", addEventListener() {}, removeEventListener() {},
                addListener() {}, removeListener() {},
            });
            window.fetch = (url) => Promise.resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve(String(url).includes("/api/send")
                    ? { sent: 1, via: { bot: "@mdtotxt_bot", chat_id: "-100999", chat_url: null, public: true } }
                    : { count: 1, messages: [] }),
            });
        },
    });
    window = dom.window;
    doc = window.document;
    await new Promise((resolve) => window.addEventListener("load", resolve));
    await sleep(60);

    check("Ohne Kanal-Link: Hinweis nennt keinen erfundenen Kanal",
        !/Kanal /.test(doc.getElementById("sendPathNote").textContent) &&
        /öffentlichen, gemeinsamen Chat/.test(doc.getElementById("sendPathNote").textContent),
        doc.getElementById("sendPathNote").textContent);
    check("Ohne Kanal-Link: Hinweis verspricht keine Löschung",
        !/gelöscht/.test(doc.getElementById("sendPathNote").textContent),
        doc.getElementById("sendPathNote").textContent);

    doc.getElementById("input").value = "Test ohne Kanal-Link";
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("Ohne Kanal-Link: Kanal-Zeile im Dialog bleibt ausgeblendet",
        doc.getElementById("sendConfirmChannelRow").hidden === true);
    check("Ohne Kanal-Link: Ziel-Fakt bleibt allgemein (und ganz)",
        doc.getElementById("sendConfirmTarget").textContent.startsWith("Öffentlicher, gemeinsamer Chat"),
        doc.getElementById("sendConfirmTarget").textContent);
    check("Ohne Kanal-Link: Bestätigungs-Knopf verspricht keinen Kanal",
        doc.getElementById("sendConfirmOkLabel").textContent === "Öffentlich senden",
        doc.getElementById("sendConfirmOkLabel").textContent);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("Ohne Kanal-Link: Erfolgsstatus nennt Bot, aber keinen Kanal",
        doc.getElementById("sendStatus").textContent.includes("@mdtotxt_bot") &&
        !doc.getElementById("sendStatus").textContent.includes("Kanal"),
        doc.getElementById("sendStatus").textContent);
    check("Ohne Kanal-Link: keine Skriptfehler", scriptErrors.length === 0,
        scriptErrors.slice(-2).join(" / "));
    dom.window.close();

    console.log(failures === 0 ? "\nJSDOM_SPEC_OK" : `\nJSDOM_SPEC_FAILED (${failures})`);
    process.exit(failures === 0 ? 0 : 1);
}

process.on("uncaughtException", (err) => {
    console.error("UNCAUGHT", err && err.stack ? err.stack.split("\n").slice(0, 4).join(" | ") : err);
    process.exit(3);
});

main().catch((err) => {
    console.error("SPEC_ERROR", err && err.stack ? err.stack.split("\n").slice(0, 6).join(" | ") : err);
    process.exit(3);
});
