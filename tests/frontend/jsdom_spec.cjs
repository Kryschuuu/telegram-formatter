/* =====================================================================
   telegram_formatter — funktionale Frontend-Smoke-Tests (jsdom)
   =====================================================================
   Ausgeführt von tests/test_jsdom_smoke.py (pytest skippt sauber, wenn
   Node/jsdom fehlen). Aufruf:  node jsdom_spec.cjs <gerendertes-index.html>

   Diese Spec prüft das Zusammenspiel von theme.js und app.js im echten DOM:
   Theme-Boot aus localStorage, Klicks auf den Switcher, Live-Vorschau,
   Debounce+Fetch gegen /api/convert, Senden, Zurücksetzen, Fehlerpfad.
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

function buildHarnessHtml() {
    let html = read(htmlPath);
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
    let sendResult = { ok: true, body: { sent: 2, results: [{ kind: "regular", status: "ok" }] } };

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

    /* Senden — seit v2.3.0 mit Bestätigungsdialog: Erst Bot/Ziel prüfen,
       abbrechen können, dann bestätigen. */
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("Dialog: öffnet sich beim Klick auf Senden",
        doc.getElementById("sendConfirm").hidden === false);
    check("Dialog: Shared-Versand wird als deaktiviert angezeigt",
        doc.getElementById("sendConfirmBot").textContent.includes("kein geteilter Bot") &&
        doc.getElementById("sendConfirmUnavailable").hidden === false,
        doc.getElementById("sendConfirmBot").textContent);
    check("Dialog: BYOB wird als Versandweg genannt",
        doc.getElementById("sendConfirmTarget").textContent.includes("BYOB"),
        doc.getElementById("sendConfirmTarget").textContent);
    check("Dialog: Nachrichtenvorschau + Zeichenzahl",
        doc.getElementById("sendConfirmPreview").textContent.includes("**viel** text") &&
        doc.getElementById("sendConfirmLength").textContent.includes("Zeichen"));
    check("Dialog: öffentliche Warnung bleibt ohne Shared-Auth verborgen",
        doc.getElementById("sendConfirmWarning").hidden === true &&
        doc.getElementById("sendConfirmPrivate").hidden === true);

    /* Abbrechen: Dialog zu, kein POST, Fokus zurück */
    doc.getElementById("sendConfirmCancel").click();
    check("Abbrechen: Dialog geschlossen", doc.getElementById("sendConfirm").hidden === true);
    check("Abbrechen: es ging nichts raus",
        !calls.some((c) => c.url.includes("api/send")),
        JSON.stringify(calls));

    /* Erneut öffnen und diesmal bestätigen */
    doc.getElementById("sendBtn").click();
    await sleep(20);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("Senden: Shared-POST wird ohne Operator-Secret nicht ausgelöst",
        !calls.some((c) => c.url.includes("api/send")));
    check("Senden: Hinweis fordert BYOB",
        sendStatus.textContent.includes("BYOB"), sendStatus.textContent);
    check("Senden: Button bleibt bedienbar", doc.getElementById("sendBtn").disabled === false);
    check("Senden: Statuszeile bekommt is-error ohne Shared-Auth",
        sendStatus.classList.contains("is-error"));

    /* Fehlerpfad (mit Dialog) */
    sendResult = { ok: false, body: { error: "Zu viele Sendeversuche", retry_after: 7, sent_before_error: 1 } };
    doc.getElementById("sendBtn").click();
    await sleep(20);
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    check("Fehler: Shared-Versand bleibt ohne Secret deaktiviert",
        sendStatus.textContent.includes("BYOB"), sendStatus.textContent);
    check("Fehler: Statuszeile bekommt is-error", sendStatus.classList.contains("is-error"));

    /* Escape schließt den Dialog wie Abbrechen */
    doc.getElementById("sendBtn").click();
    await sleep(20);
    check("Escape-Vorbereitung: Dialog offen", doc.getElementById("sendConfirm").hidden === false);
    doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    check("Escape: Dialog geschlossen ohne Senden",
        doc.getElementById("sendConfirm").hidden === true &&
        calls.filter((c) => c.url.includes("api/send")).length === 0,
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
    doc.getElementById("sendConfirmOk").click();
    await sleep(60);
    const sendCall = byobCalls.filter((u) => u.includes("/api/byob/send")).pop();
    check("BYOB: Senden geht an /api/byob/send", Boolean(sendCall));
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
    check("BYOB: Senden-Button-Label zurück auf geteilten Bot",
        doc.getElementById("sendBtnLabel").textContent === "An Telegram senden");

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
