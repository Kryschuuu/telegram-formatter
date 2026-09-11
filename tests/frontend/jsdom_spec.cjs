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
        `<script>${read(path.join(STATIC_DIR, "js/app.js"))}</script></body>`
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
    const doc = window.document;
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

    /* Senden */
    doc.getElementById("sendBtn").click();
    await sleep(60);
    check("Senden: POST /api/send mit aktuellem Text",
        calls.some((c) => c.url.includes("api/send") && String(c.body).includes("**viel** text")));
    check("Senden: Erfolgs-Status mit Anzahl", sendStatus.textContent.includes("2 Nachricht(en) gesendet"),
        sendStatus.textContent);
    check("Senden: Button danach wieder aktiv", doc.getElementById("sendBtn").disabled === false);
    check("Senden: Statuszeile bekommt is-ok", sendStatus.classList.contains("is-ok"));

    /* Fehlerpfad */
    sendResult = { ok: false, body: { error: "Zu viele Sendeversuche", retry_after: 7, sent_before_error: 1 } };
    doc.getElementById("sendBtn").click();
    await sleep(60);
    check("Fehler: Meldung + Wartezeit + Teilsende-Hinweis",
        sendStatus.textContent.includes("Zu viele Sendeversuche") &&
        sendStatus.textContent.includes("Warte 7 s") &&
        sendStatus.textContent.includes("Bereits gesendet: 1"),
        sendStatus.textContent);
    check("Fehler: Statuszeile bekommt is-error", sendStatus.classList.contains("is-error"));

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
