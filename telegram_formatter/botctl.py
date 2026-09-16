"""
telegram_formatter/botctl.py
============================
Kommandozeilen-Werkzeug für eigene, dezentrale Telegram-Bots.

Anders als ``cli.py`` (das nur konvertiert und mit *einem* zentral konfigurierten
Bot sendet) arbeitet ``botctl`` mit dem **Bot des Nutzers**: Registrierung
verifizieren, Code reviewen, Freigabe erteilen und in einer ephemeren Session
senden — ohne dass ein Token oder Nachrichteninhalt gespeichert wird.

Befehle::

    # 1) Eigener Bot: Token gegen Telegram prüfen (nichts wird gespeichert)
    python -m telegram_formatter.botctl register --token-env TELEGRAM_BOT_TOKEN --owner alice

    # 2) Bot-Code statisch prüfen und Review-Ticket anlegen
    python -m telegram_formatter.botctl review examples/own_bot/minimal_bot.py --bot-id 123456789

    # 3) Zwei Freigaben (Vier-Augen-Prinzip, mindestens eine von Maintainer:in)
    python -m telegram_formatter.botctl approve RV-1A2B3C4D --reviewer alice --role maintainer \\
        --checks C1,C2,C3,C4,C5,C6,C7,C8,C9
    python -m telegram_formatter.botctl approve RV-1A2B3C4D --reviewer bob --role contributor \\
        --checks C1,C2,C3,C4,C5,C6,C7,C8,C9

    # 4) In einer Session senden (Token nur für diese Session im RAM)
    python -m telegram_formatter.botctl send --chat-id -1001234567890 --file beispiel_input.txt \\
        --bot-source examples/own_bot/minimal_bot.py --send

Der Review-Stand liegt in einer Metadaten-Datei (Standard:
``audit/reviews.json``) — dort stehen ausschließlich Ticket-IDs, Bot-IDs,
Prüfsummen, Regel-IDs und Entscheidungen, **keine** Inhalte und **keine**
Tokens. Diese Datei gehört ins Repository (Ordner ``audit/``); sie ist der
Audit-Trail. Details: ``audit/README.md``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from telegram_formatter.botkit.privacy import install_privacy_filters, scrub_environment
from telegram_formatter.botkit.registry import (
    OWNER_REF_PATTERN,
    BotRegistry,
    RegistrationError,
)
from telegram_formatter.botkit.review import (
    CHECKLIST,
    CHECKLIST_IDS,
    Reviewer,
    ReviewError,
    ReviewGate,
    ReviewGateError,
    ReviewLedger,
    ReviewRole,
    source_sha256,
)
from telegram_formatter.botkit.session import SessionConfig, SessionError, SessionManager
from telegram_formatter.botkit.telegram_api import TelegramAPIError, get_me
from telegram_formatter.botkit.tokens import BotToken, TokenError

DEFAULT_LEDGER = "audit/reviews.json"

LOGGER = logging.getLogger("botctl")


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def _configure_logging(verbose: bool) -> None:
    """Installiert die Privacy-Filter *vor* jedem Log-Aufruf."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    install_privacy_filters()


def _load_ledger(path: str) -> ReviewLedger:
    return ReviewLedger.load(path)


@contextmanager
def _ledger_transaction(path: str):
    """
    Load-Modify-Save des Audit-Trails unter einer Prozess-Sperre (Audit B-10).

    Ohne Sperrdatei galt "last writer wins": zwei gleichzeitige
    ``botctl approve`` verloren die jeweils andere Entscheidung.
    Geflockt wird eine Sidecar-Datei ``<ledger>.lock``; Plattformen ohne
    ``fcntl`` (Windows) laufen ungesperrt weiter (best effort, dokumentiert).
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield _load_ledger(path)
        return

    target = Path(path)
    lock_path = target.with_name(target.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            yield _load_ledger(path)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _gate(ledger: ReviewLedger) -> ReviewGate:
    # Lokaler/Git-Modus: das Ledger ist autoritativ, keine Registry nötig.
    return ReviewGate(ledger, registry=None, min_approvals=2, require_maintainer=True)


def _read_input(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def _resolve_owner(explicit: str | None) -> str:
    """
    Owner-Handle bestimmen — mit sicherem Fallback (Audit B-8).

    Der Default war ``os.environ["USER"][:32]``; realistische Usernamen
    (Leerzeichen, Umlaute, Suffixe wie ``$jones``) verletzen
    OWNER_REF_PATTERN und führten zur irreführenden Meldung
    „Bot konnte nicht verifiziert werden". Unpassende Werte werden auf
    das neutrale Pseudonym ``local`` zurückgezogen.
    """
    candidate = (explicit or os.environ.get("USER", "") or "local").strip()[:32]
    return candidate if OWNER_REF_PATTERN.match(candidate) else "local"


# --------------------------------------------------------------------------- #
# Befehle
# --------------------------------------------------------------------------- #
def cmd_register(args: argparse.Namespace) -> int:
    """Verifiziert ein Token gegen Telegram — ohne es zu speichern."""
    try:
        token = BotToken.from_environment(args.token_env)
    except TokenError as exc:
        print(f"✖ {exc}")
        return 2

    registry = BotRegistry(
        verify=lambda secret: get_me(secret, timeout=args.timeout, api_base=args.api_base)
    )
    try:
        record = registry.register(token, owner_ref=args.owner)
    except RegistrationError as exc:
        print(f"✖ Registrierung abgelehnt: {exc}")
        return 1

    # Token-Umgebungsvariable sofort entfernen: keine Vererbung an Kindprozesse.
    removed = list(scrub_environment(args.token_env))
    print("✔ Bot verifiziert (Token wird nicht gespeichert):")
    print(f"    Bot-ID   : {record.identity.bot_id}")
    print(f"    Handle   : {record.identity.handle}")
    print(f"    Besitzer : {record.owner_ref} (fp={record.owner_fingerprint})")
    print(f"    Status   : {record.status.value}")
    if removed:
        print(f"    Hinweis  : ${args.token_env} wurde aus dem Environment entfernt.")
    print("\nNächster Schritt: Bot-Code reviewen lassen")
    print(f"    python -m telegram_formatter.botctl review <bot-code.py> --bot-id {record.identity.bot_id}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Statische Analyse + Review-Ticket (oder nur Bericht mit --check)."""
    path = Path(args.path)
    if not path.exists():
        print(f"✖ Datei nicht gefunden: {path}")
        return 2

    with _ledger_transaction(args.ledger) as ledger:
        gate = _gate(ledger)
        try:
            report = gate.analyze(path)
        except (SyntaxError, UnicodeDecodeError, ValueError, OSError) as exc:
            # Das Review-Tor prüft Python-Quellcode. Nicht-parsbare Eingaben
            # (z. B. Markdown-READMEs im bots/-Ordner, Binärdateien,
            # Verzeichnisse — Audit B-8: IsADirectoryError lief bisher als
            # rohes Traceback hoch) werden als Eingabefehler gemeldet.
            ort = f" in Zeile {exc.lineno}" if isinstance(exc, SyntaxError) else ""
            print(
                f"✖ {path}: nicht als Python-Quelltext lesbar{ort} "
                f"({exc.__class__.__name__}) — nur *.py-Dateien zum Review einreichen."
            )
            return 2

        print(report.as_text())
        if not report.ok:
            print("\n✖ Review gestoppt: Blocker müssen behoben werden.")
            return 1

        if args.check:
            print("\n✔ Nur Prüfung gewünscht (--check): kein Ticket angelegt.")
            return 0

        try:
            ticket = gate.submit(args.bot_id, path)
        except ReviewGateError as exc:
            print(f"\n✖ {exc}")
            return 1

        ledger.save(args.ledger)
        print(f"\n✔ Ticket {ticket.ticket_id} angelegt (sha256={ticket.source_sha256[:12]}).")
        print(f"    Audit-Trail: {args.ledger}")
        print("    Freigabe durch zwei Personen, davon mindestens eine Maintainer:in:")
        print(f"    python -m telegram_formatter.botctl approve {ticket.ticket_id} --reviewer <handle> "
              f"--role maintainer --checks {','.join(sorted(CHECKLIST_IDS))}")
        return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Trägt eine Review-Entscheidung ein (Freigabe oder Ablehnung)."""
    try:
        reviewer = Reviewer(args.reviewer, ReviewRole(args.role))
    except (ReviewError, ValueError) as exc:
        print(f"✖ {exc}")
        return 2

    if args.reject:
        with _ledger_transaction(args.ledger) as ledger:
            gate = _gate(ledger)
            try:
                ticket = gate.reject(args.ticket, reviewer, note=args.note or "ohne Angabe")
            except (ReviewError, RegistrationError) as exc:
                print(f"✖ {exc}")
                return 1
            ledger.save(args.ledger)
            print(f"✔ Ticket {ticket.ticket_id} abgelehnt von {reviewer.handle}.")
            return 0

    checks = tuple(c.strip() for c in args.checks.split(",") if c.strip())
    missing = CHECKLIST_IDS - set(checks)
    if missing:
        print("✖ Checkliste unvollständig. Es fehlen: " + ", ".join(sorted(missing)))
        print("  Vollständige Liste:")
        for item in CHECKLIST:
            print(f"    {item.check_id}: {item.question}")
        return 2

    with _ledger_transaction(args.ledger) as ledger:
        gate = _gate(ledger)
        try:
            ticket = gate.approve(args.ticket, reviewer, checks=checks, note=args.note)
        except (ReviewError, RegistrationError) as exc:
            # RegistrationError (Audit B-9): wenn das Ticket die Freigabe in
            # eine Registry schreiben will, deren Eintrag revoked/abgelaufen
            # ist — sauber melden statt Traceback.
            print(f"✖ {exc}")
            return 1
        ledger.save(args.ledger)
    state = "freigegeben ✔" if ticket.is_approved(
        min_approvals=gate.min_approvals, require_maintainer=gate.require_maintainer
    ) else f"wartet ({len(ticket.approvals)}/{gate.min_approvals} Freigaben)"
    print(f"✔ Entscheidung von {reviewer.handle} ({reviewer.role.value}) eingetragen — {state}.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Prüft, ob Bot und Code-Fassung freigegeben sind (CI-Tor vor dem Deployment)."""
    with _ledger_transaction(args.ledger) as ledger:
        gate = _gate(ledger)
        try:
            gate.verify(args.bot_id, args.path, check_registry=False)
        except (ReviewGateError, OSError, ValueError) as exc:
            print(f"✖ Nicht freigegeben: {exc}")
            return 1
    print(f"✔ Freigegeben: {args.path} (sha256={source_sha256(args.path)[:12]}).")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    """Öffnet eine ephemere Session und versendet den Inhalt über den eigenen Bot."""
    if args.bot_source and args.local_trust:
        # Widerspruch (seit v2.11.1 explizit): --bot-source verlangt das
        # Review-Gate, --local-trust entfernt es — die Kombination scheiterte
        # zuvor kryptisch beim Session-Öffnen („kein ReviewGate konfiguriert“).
        print("✖ --bot-source und --local-trust schließen sich aus: entweder reviewter")
        print("  Bot-Code (--bot-source <datei>) oder ausdrücklicher Lokalvertrauen")
        print("  (--local-trust, nur für eigene, private Bots).")
        return 2
    try:
        token = BotToken.from_environment(args.token_env)
    except TokenError as exc:
        print(f"✖ {exc}")
        return 2

    # Review-Pflicht: entweder freigegebener Bot-Code oder explizites
    # --local-trust (selbst gehostet/privat). Standard ist fail-closed.
    gate = _gate(_load_ledger(args.ledger))
    if args.bot_source:
        try:
            gate.verify(token.bot_id, args.bot_source, check_registry=False)
        except ReviewGateError as exc:
            print(f"✖ Review fehlt: {exc}")
            return 1
        print(f"✔ Review bestätigt für {args.bot_source}.")
    elif not args.local_trust:
        print("✖ Weder --bot-source (reviewter Code) noch --local-trust angegeben.")
        print("  Für private/self-hosted Bots: --local-trust (eigene Verantwortung).")
        return 2
    else:
        print("⚠ --local-trust: Review wird übersprungen (nur für eigene, private Bots).")

    registry = BotRegistry(
        verify=lambda secret: get_me(secret, timeout=args.timeout, api_base=args.api_base)
    )
    try:
        registry.register(token, owner_ref=_resolve_owner(args.owner))
    except RegistrationError as exc:
        print(f"✖ Bot konnte nicht verifiziert werden: {exc}")
        return 1

    config = SessionConfig(
        require_review=bool(args.bot_source),
        timeout=args.timeout,
        api_base=args.api_base,
    )
    # Audit B-2: Der --local-trust-Pfad darf dem Manager keinen ReviewGate
    # geben — sonst verlangt SessionManager.open einen Registry-Status
    # "approved", den im Selbstbetrieb niemand setzt, und jede Session
    # scheitert ("Bot ist nicht freigegeben"). Ohne Gate gilt: verifizierter
    # Bot + ausdrücklicher Lokalvertrauen des Nutzers genügen.
    # Eingabe *vor* dem Session-Öffnen lesen (seit v2.11.1): Eine unlesbare
    # Datei meldet sich sauber (Exit 2) und hinterlässt keine geöffnete,
    # nie geschlossene Session — zuvor lief hier ein roher Traceback hoch.
    try:
        text = _read_input(args.file)
    except (OSError, ValueError) as exc:  # UnicodeDecodeError ⊂ ValueError
        print(f"✖ Eingabe nicht lesbar ({exc.__class__.__name__}).")
        return 2

    manager = SessionManager(
        registry=registry,
        review_gate=None if args.local_trust else gate,
        config=config,
    )

    try:
        session = manager.open(token, args.chat_id, source_path=args.bot_source)
    except SessionError as exc:
        print(f"✖ Session nicht geöffnet: {exc}")
        return 1

    try:
        with session:
            if not args.send:
                from telegram_formatter.utils import build_messages

                messages = build_messages(text, session.chat_id)
                print(f"Dry-Run: {len(messages)} Nachricht(en) gebaut "
                      f"({sum(1 for m in messages if m.kind == 'rich')} Rich, "
                      f"{sum(1 for m in messages if m.kind == 'regular')} Regular).")
                for index, message in enumerate(messages, 1):
                    print(f"  [{index}] {message.kind}: {str(message.payload)[:160]}…")
                print("\nZum Senden '--send' ergänzen.")
                return 0

            responses = session.send(text)
            print(f"✔ Gesendet: {len(responses)} Chunk(s) über Bot {session.bot_id}.")
            print(f"  Session {session.session_id[:8]} wurde geschlossen (Token verworfen).")
            return 0
    except SessionError as exc:
        print(f"✖ Versand abgebrochen: {exc}")
        return 1
    finally:
        scrub_environment(args.token_env)


def cmd_checklist(_args: argparse.Namespace) -> int:
    """Gibt die Review-Checkliste aus (auch für PR-Beschreibungen)."""
    print("Review-Checkliste (alle Punkte sind für eine Freigabe erforderlich):\n")
    for item in CHECKLIST:
        print(f"  [{item.check_id}] {item.question}")
        print(f"        Warum: {item.why}")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="botctl",
        description="Eigene, dezentrale Telegram-Bots registrieren, reviewen und nutzen.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Logging.")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER,
                        help=f"Audit-Trail (Metadaten) — Standard: {DEFAULT_LEDGER}")
    sub = parser.add_subparsers(dest="command", required=True)

    # --ledger soll vor *oder* nach dem Unterbefehl funktionieren.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ledger", default=argparse.SUPPRESS,
                        help=f"Audit-Trail (Metadaten) — Standard: {DEFAULT_LEDGER}")

    p_register = sub.add_parser("register", parents=[common], help="Bot-Token gegen Telegram verifizieren.")
    p_register.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    p_register.add_argument("--owner", required=True, help="Pseudonym (z. B. GitHub-Handle).")
    p_register.add_argument("--timeout", type=float, default=10.0)
    p_register.add_argument("--api-base", default="https://api.telegram.org",
                            help="Alternative API-Basis (Tests, private Bot-API-Server).")
    p_register.set_defaults(func=cmd_register)

    p_review = sub.add_parser("review", parents=[common], help="Bot-Code statisch prüfen und Ticket anlegen.")
    p_review.add_argument("path")
    p_review.add_argument("--bot-id", type=int, required=True)
    p_review.add_argument("--check", action="store_true", help="Nur prüfen, kein Ticket.")
    p_review.set_defaults(func=cmd_review)

    p_approve = sub.add_parser("approve", parents=[common], help="Freigabe/Ablehnung eintragen.")
    p_approve.add_argument("ticket")
    p_approve.add_argument("--reviewer", required=True)
    p_approve.add_argument("--role", choices=["maintainer", "contributor"], default="contributor")
    p_approve.add_argument("--checks", default="", help="Kommaliste, z. B. C1,C2,...,C9")
    p_approve.add_argument("--note", default="")
    p_approve.add_argument("--reject", action="store_true", help="Ablehnung statt Freigabe.")
    p_approve.set_defaults(func=cmd_approve)

    p_verify = sub.add_parser("verify", parents=[common], help="Freigabe vor dem Deployment prüfen.")
    p_verify.add_argument("path")
    p_verify.add_argument("--bot-id", type=int, required=True)
    p_verify.set_defaults(func=cmd_verify)

    p_send = sub.add_parser("send", parents=[common], help="In einer ephemeren Session über den eigenen Bot senden.")
    p_send.add_argument("--chat-id", required=True)
    p_send.add_argument("--file", help="Eingabedatei (sonst STDIN).")
    p_send.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    p_send.add_argument("--owner", default=None,
                        help="Pseudonym; Standard: $USER (falls gültig), sonst 'local'.")
    p_send.add_argument("--bot-source", help="Reviewter Bot-Code (Pfad).")
    p_send.add_argument("--local-trust", action="store_true",
                        help="Review überspringen (nur private/self-hosted Bots).")
    p_send.add_argument("--send", action="store_true", help="Wirklich senden (sonst Dry-Run).")
    p_send.add_argument("--timeout", type=float, default=10.0)
    p_send.add_argument("--api-base", default="https://api.telegram.org",
                        help="Alternative API-Basis (Tests, private Bot-API-Server).")
    p_send.set_defaults(func=cmd_send)

    p_list = sub.add_parser("checklist", parents=[common], help="Review-Checkliste ausgeben.")
    p_list.set_defaults(func=cmd_checklist)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return int(args.func(args))
    except TelegramAPIError as exc:
        print(f"✖ Telegram-API: {exc}")
        return 1
    except (ReviewError, RegistrationError, SessionError, TokenError) as exc:
        # Sicherheitsnetz (seit v2.11.1): bekannte Domänenfehler — z. B.
        # korrupter Audit-Trail oder fehlende Freigabe — melden sich sauber
        # statt als Traceback. Alle Meldungen sind per Konstruktion
        # token- und inhaltsfrei.
        print(f"✖ {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
