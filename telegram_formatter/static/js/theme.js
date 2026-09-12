/* =====================================================================
   telegram_formatter — Theme-Switcher
   =====================================================================
   Wird im <head> synchron geladen (kein defer!): der Boot-Schritt setzt
   data-theme am <html>-Element, BEVOR der<body> gemalt wird — so gibt es
   keinen sichtbaren Themawechsel („Flash of wrong theme").

   Funktionsweise (Details: docs/DESIGN.md, Abschnitt Theme-Switcher):
     1. Gewählte Präferenz liegt in localStorage unter "tf-theme"
        (Werte: auto | light | dark | colorful | minimal; Default: auto).
     2. apply() spiegelt die Präferenz ins Attribut <html data-theme=...>.
        Die eigentliche Färbung passiert NUR in CSS (tokens.css) — dieses
        Skript kennt keine Farbwerte außer den theme-color-Meta-Farben.
     3. "auto" wird nicht aufgelöst: CSS regelt es über
        @media (prefers-color-scheme), reagiert also live auf das
        Betriebssystem. meta[name=theme-color] (Browser-Oberfläche/
        Handy-Statusbar) wird dagegen per matchMedia aufgelöst.
     4. Nach DOMContentLoaded verdrahtet init() die Buttons des Switchers
        (delegierte Klicks auf #themeSwitcher).

   CSP: externes Skript von 'self', keine Inline-Snippets.
   Kein localStorage (Privacy-Modus, file://): alles läuft trotzdem, nur
   ohne Persistenz — try/catch um getItem/setItem.
   ===================================================================== */
(function () {
    "use strict";

    var STORAGE_KEY = "tf-theme";
    var CHOICES = ["auto", "light", "dark", "colorful", "minimal"];

    /* Browser-Oberfläche (Adressleiste, Statusbar) in Theme-Nähe färben.
       Für "auto" wird die aktuelle System-Voreinstellung aufgelöst. */
    var META_COLORS = {
        light: "#eef2f7",
        dark: "#0e1621",
        colorful: "#17123a",
        minimal: "#ffffff"
    };

    function storedChoice() {
        try {
            var value = window.localStorage.getItem(STORAGE_KEY);
            return CHOICES.indexOf(value) === -1 ? "auto" : value;
        } catch (err) {
            return "auto";
        }
    }

    function prefersDark() {
        return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    }

    function apply(choice) {
        if (CHOICES.indexOf(choice) === -1) {
            choice = "auto";
        }
        document.documentElement.setAttribute("data-theme", choice);

        var color = choice === "auto"
            ? (prefersDark() ? META_COLORS.dark : META_COLORS.light)
            : META_COLORS[choice];
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta && color) {
            meta.setAttribute("content", color);
        }
    }

    function setPressed(switcher, choice) {
        var buttons = switcher.querySelectorAll("[data-theme-choice]");
        for (var i = 0; i < buttons.length; i++) {
            var isActive = buttons[i].getAttribute("data-theme-choice") === choice;
            buttons[i].classList.toggle("is-active", isActive);
            buttons[i].setAttribute("aria-pressed", isActive ? "true" : "false");
        }
    }

    /* --- Boot: läuft synchron im <head>, noch ohne <body> -------------- */
    var initial = storedChoice();
    apply(initial);

    /* Reagiere auf Systemwechsel, solange "auto" aktiv ist (nur Meta-Farbe;
       die Seite selbst wird bereits rein über die Media-Query in tokens.css
       umgefärbt). */
    if (window.matchMedia) {
        var mq = window.matchMedia("(prefers-color-scheme: dark)");
        var onSystemChange = function () {
            if (storedChoice() === "auto") {
                apply("auto");
            }
        };
        if (typeof mq.addEventListener === "function") {
            mq.addEventListener("change", onSystemChange);
        } else if (typeof mq.addListener === "function") {
            mq.addListener(onSystemChange); /* ältere Safari-Generationen */
        }
    }

    /* --- Schaltflächen verdrahten ---------------------------------------- */
    function init() {
        var switcher = document.getElementById("themeSwitcher");
        if (!switcher) {
            return;
        }
        /* Ab ≥ 640px zeigen die Buttons zusätzlich ihr Textlabel. */
        if (window.matchMedia && window.matchMedia("(min-width: 40rem)").matches) {
            switcher.classList.add("is-rich");
        }
        setPressed(switcher, storedChoice());

        switcher.addEventListener("click", function (event) {
            var button = event.target.closest
                ? event.target.closest("[data-theme-choice]")
                : null;
            if (!button || !switcher.contains(button)) {
                return;
            }
            var choice = button.getAttribute("data-theme-choice");
            try {
                window.localStorage.setItem(STORAGE_KEY, choice);
            } catch (err) {
                /* ohne Persistenz weiterschalten — Funktionalität bleibt erhalten */
            }
            apply(choice);
            setPressed(switcher, choice);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
