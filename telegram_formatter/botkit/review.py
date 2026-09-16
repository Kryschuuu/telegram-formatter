"""
telegram_formatter/botkit/review.py
===================================
Peer-Review-Gate für Nutzer-Bots — der Teil des Designs, der verhindert,
dass „jedermann darf einen Bot bauen" zu „jedermann darf Code ausführen" wird.

Drei Ebenen, die zusammenwirken:

1. **Automatische Statik** (:func:`analyze_source`) — AST-basierte Regelprüfung
   gegen die harten No-Gos: Persistenz, Fremdnetzwerk, dynamische
   Code-Ausführung, hartkodierte Secrets, Inhalte in Logs.
2. **Menschliche Abnahme** (:class:`ReviewLedger`) — Tickets, zwei
   voneinander unabhängige Freigaben (Vier-Augen-Prinzip), mindestens eine
   davon von einem Maintainer, vollständig ausgefüllte Checkliste.
3. **Bindung an den Code** (:class:`ReviewGate`) — die Freigabe gilt nur für
   die exakte Prüfsumme der reviewten Datei. Eine Zeile Änderung ⇒ neues
   Review.

Das Ledger speichert ausschließlich Metadaten (Prüfsummen, Handles,
Zeitstempel, Regel-IDs) — keine Quelltexte, keine Inhalte.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from .privacy import audit
from .registry import BotRegistry

__all__ = [
    "CHECKLIST",
    "RULES",
    "CheckItem",
    "Finding",
    "ReviewDecision",
    "ReviewError",
    "ReviewGate",
    "ReviewGateError",
    "ReviewLedger",
    "ReviewReport",
    "ReviewRole",
    "ReviewTicket",
    "Reviewer",
    "analyze_code",
    "analyze_source",
    "source_sha256",
]

LOGGER = logging.getLogger("botkit.review")

# --------------------------------------------------------------------------- #
# Regelwerk der statischen Analyse
# --------------------------------------------------------------------------- #
BLOCKER = "blocker"
WARNING = "warning"

#: Module, die in einem Nutzer-Bot nichts verloren haben
#: (Persistenz, Shell, Roh-Sockets, Fremd-Storage, Deserialisierung,
#: Reflektion — importlib/tempfile/shutil/builtins wurden in den
#: Bypass-Reproduktionen des Audits H-1 als Einfallstore bestätigt).
FORBIDDEN_IMPORTS = frozenset(
    {
        "sqlite3", "shelve", "pickle", "marshal", "dill", "joblib",
        "subprocess", "socket", "smtplib", "ftplib", "telnetlib", "paramiko",
        "redis", "pymongo", "boto3", "botocore", "psycopg2", "pymysql",
        "elasticsearch", "kafka", "sqlalchemy", "csv", "xlwt", "openpyxl",
        "tempfile", "importlib", "shutil", "builtins",
    }
)

#: Einzige erlaubte Gegenstelle für ausgehende HTTP-Aufrufe.
ALLOWED_HTTP_HOSTS = ("api.telegram.org",)

#: Methoden, die ohne Prüfung der URL kritisch sind.
HTTP_CALL_NAMES = frozenset({"get", "post", "put", "patch", "delete", "request", "urlopen"})

#: Modulwurzeln, deren HTTP-Aufrufe URL-geprüft werden müssen (nach Alias-Auflösung).
HTTP_MODULE_ROOTS = frozenset({"requests", "httpx", "urllib", "http", "aiohttp"})

#: Namen, hinter denen in Logs typischerweise Nachrichteninhalte stecken.
SENSITIVE_LOG_NAMES = frozenset(
    {
        "text", "message", "messages", "msg", "content", "body", "payload",
        "markdown", "update", "chat_text", "raw", "caption", "document",
    }
)

#: Aufrufe, die Inhalte auf ein dauerhaftes Medium schreiben.
PERSISTENCE_CALLS = frozenset(
    {
        "write_text", "write_bytes", "writelines", "to_csv", "to_excel",
        "to_json", "save", "dump", "connect", "set", "setex", "insert_one",
        "insert_many", "put_object", "remove", "unlink", "rmtree", "makedirs",
    }
)

#: Dynamische Code-Ausführung / Deserialisierung.
DYNAMIC_EXEC_CALLS = frozenset({"eval", "exec", "compile", "__import__", "loads"})

#: Shell- und Prozessausführung.
SHELL_CALLS = frozenset(
    {"system", "popen", "run", "call", "check_call", "check_output", "getoutput", "Popen"}
)

#: ``os.open``/``io.open``-Flags, die Schreibzugriff oder Persistenz bedeuten
#: (für BK002, R-2 — Deskriptor-Persistenz statt Dateinamen-Pfad).
_WRITE_FLAG_NAMES = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"})

#: Logger-Methoden, deren Argumente auf Inhalte geprüft werden.
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})


@dataclass(frozen=True)
class Rule:
    """Eine statische Regel (Metadaten für Reports und Doku)."""

    rule_id: str
    severity: str
    title: str
    hint: str


RULES: dict[str, Rule] = {
    rule.rule_id: rule
    for rule in (
        Rule("BK001", BLOCKER, "Verbotener Import", "Modul entfernen; botkit-API nutzen."),
        Rule("BK002", BLOCKER, "Persistenz-Schreibzugriff", "Keine Datei-/DB-/Cache-Schreibzugriffe."),
        Rule("BK003", BLOCKER, "Dynamische Code-Ausführung", "eval/exec/pickle sind untersagt."),
        Rule("BK004", BLOCKER, "Netzwerk zu Fremd-Host", "Nur api.telegram.org darf kontaktiert werden."),
        Rule("BK005", BLOCKER, "Inhalte im Log", "audit() mit Metadaten statt Inhalten loggen."),
        Rule("BK006", BLOCKER, "Hartkodiertes Geheimnis", "Token/Schlüssel nie im Quelltext."),
        Rule("BK007", BLOCKER, "Shell-/Prozessausführung", "Keine externen Prozesse starten."),
        Rule("BK008", BLOCKER, "Eigener Netzwerk-Server", "Webhook nur hinter geprüftem TLS-Terminator."),
        Rule("BK010", BLOCKER, "Nicht prüfbare Ziel-URL",
             "URL als Konstante übergeben (nur api.telegram.org)."),
        Rule("BK011", WARNING, "Ausgabe von Inhalten", "print() von Inhalten vermeiden."),
        Rule("BK012", WARNING, "Unsicherer Zufall", "secrets statt random für Token/Noncen."),
    )
}


@dataclass(frozen=True)
class Finding:
    """Ein Regelverstoß an einer konkreten Stelle."""

    rule_id: str
    severity: str
    file: str
    line: int
    message: str
    hint: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} [{self.rule_id}/{self.severity}] {self.message}"


@dataclass
class ReviewReport:
    """Ergebnis der statischen Analyse einer Bot-Quelldatei."""

    file: str
    source_sha256: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        """Findings, die ein Deployment verhindern."""
        return [f for f in self.findings if f.severity == BLOCKER]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def as_text(self) -> str:
        """Menschenlesbarer Report für CLI, PR-Kommentar und CI-Log."""
        if not self.findings:
            return f"✔ {self.file} — keine Befunde (sha256={self.source_sha256[:12]})."
        lines = [
            f"{'✖' if not self.ok else '⚠'} {self.file} — "
            f"{len(self.blocking)} Blocker, {len(self.warnings)} Warnungen "
            f"(sha256={self.source_sha256[:12]})"
        ]
        lines.extend(f"  {finding}" for finding in self.findings)
        if not self.ok:
            lines.append("  → Blocker müssen vor dem Review behoben werden.")
        return "\n".join(lines)


def source_sha256(path: str | Path) -> str:
    """SHA-256 über die *Bytes* der Datei — Grundlage der Freigabebindung."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# AST-Analyse
# --------------------------------------------------------------------------- #
def _dotted(node: ast.AST) -> str:
    """Baut aus ``a.b.c`` den String ``"a.b.c"`` (so weit möglich)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _names_in(node: ast.AST) -> set[str]:
    """Alle Bezeichner in einem Teilbaum (für Inhalts-Heuristiken)."""
    return {
        n.id.lower()
        for n in ast.walk(node)
        if isinstance(n, ast.Name)
    } | {
        n.attr.lower()
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
    }


def _suppressed(source_lines: Sequence[str], lineno: int, rule_id: str) -> bool:
    """
    Prüft ``# botkit:allow BK00x[,BK00y]`` auf der Zeile selbst oder direkt
    darüber. Suppressions sind bewusst sichtbar (Reviewer sehen sie sofort).
    """
    for index in (lineno - 1, lineno - 2):
        if 0 <= index < len(source_lines):
            line = source_lines[index]
            if "#" not in line:
                continue
            marker = line.split("#", 1)[1].strip()
            if marker.startswith("botkit:allow"):
                rest = marker[len("botkit:allow"):].strip().lstrip(":= ")
                allowed_ids = {part.strip() for part in rest.replace(",", " ").split()}
                if rule_id in allowed_ids or "all" in allowed_ids:
                    return True
    return False


#: Token-artige Literal überall im Text (unverankert — fängt auch
#: eingebettete und ann_assignierte Secrets; Audit H-1/C).
_TOKEN_EMBEDDED = re.compile(r"\d{5,16}:[A-Za-z0-9_-]{35}")


@dataclass
class _ModuleFacts:
    """
    Kontextwissen aus dem Modul, damit Alias-Imports die Regeln nicht hebeln.

    Audit H-1 reproduzierte Bypasses via ``import requests as rq``,
    ``from os import system`` und variablen URLs; alle drei laufen über
    diese Zuordnungen.
    """

    #: lokaler Name → Modulwurzel (``rq`` → ``requests``, ``sock`` → ``socket``)
    root_aliases: dict[str, str] = field(default_factory=dict)
    #: from-import-Name → punktierter Pfad (``system`` → ``os.system``)
    name_paths: dict[str, str] = field(default_factory=dict)
    #: einfach zugewiesene Modul-Konstanten (Name → Stringwert)
    str_consts: dict[str, str] = field(default_factory=dict)


def collect_facts(tree: ast.Module) -> _ModuleFacts:
    """1-Pass-Vorlauf: Imports, Session-Objekte, eindeutige String-Konstanten."""
    facts = _ModuleFacts()

    store_counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            store_counts[node.id] = store_counts.get(node.id, 0) + 1

    # Imports auf allen Ebenen (auch funktionslokal — Nutzer bots importieren gern spät).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                facts.root_aliases[alias.asname or top] = top
        elif isinstance(node, ast.ImportFrom) and not node.level:
            root = (node.module or "").split(".", 1)[0]
            for alias in node.names:
                facts.name_paths[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}" if node.module else alias.name
                )
            if root:
                facts.root_aliases.setdefault(root, root)

    # Modul-Ebene: String-Konstanten (Assign + AnnAssign) und Session-Objekte.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name) and store_counts.get(t.id, 0) == 1:
                        facts.str_consts[t.id] = node.value.value
            elif isinstance(node.value, ast.Call):
                dotted = _dotted(node.value.func)
                tail = dotted.rsplit(".", 1)[-1]
                if tail in {"Session", "Client", "AsyncClient"} and "." in dotted:
                    root = facts.root_aliases.get(dotted.split(".", 1)[0], dotted.split(".", 1)[0])
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            facts.root_aliases[t.id] = root
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and isinstance(node.target, ast.Name):
            if store_counts.get(node.target.id, 0) == 1:
                facts.str_consts[node.target.id] = node.value.value
    return facts


def _has_write_flags(expr: ast.AST) -> bool:
    """``True``, wenn ``expr`` einen Schreib-Flag enthält (z. B. ``os.O_WRONLY``)."""
    return any(
        isinstance(node, ast.Attribute) and node.attr in _WRITE_FLAG_NAMES
        for node in ast.walk(expr)
    )


class _SecurityVisitor(ast.NodeVisitor):
    """Sammelt Regelverstöße in einem Modul (rein, ohne I/O)."""

    def __init__(self, filename: str, source: str, facts: _ModuleFacts | None = None) -> None:
        self.filename = filename
        self.lines = source.splitlines()
        self.findings: list[Finding] = []
        self.facts = facts or _ModuleFacts()

    # -- Hilfsfunktionen ----------------------------------------------------
    def _add(self, rule_id: str, node: ast.AST, message: str) -> None:
        rule = RULES[rule_id]
        if _suppressed(self.lines, getattr(node, "lineno", 0), rule_id):
            return
        self.findings.append(
            Finding(
                rule_id=rule_id,
                severity=rule.severity,
                file=self.filename,
                line=getattr(node, "lineno", 0),
                message=message,
                hint=rule.hint,
            )
        )

    # -- Importe ------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in FORBIDDEN_IMPORTS:
                self._add("BK001", node, f"Import von '{alias.name}' ist nicht erlaubt.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root in FORBIDDEN_IMPORTS:
            self._add("BK001", node, f"Import aus '{node.module}' ist nicht erlaubt.")
        if root == "random":
            self._add("BK012", node, "'random' ist kryptographisch unsicher — 'secrets' nutzen.")
        self.generic_visit(node)

    # -- Zuweisungen (hartkodierte Secrets) ---------------------------------
    def _check_secret_literal(self, node: ast.AST, literal: str, targets: str) -> None:
        if _TOKEN_EMBEDDED.search(literal):
            self._add("BK006", node, "Bot-Token im Quelltext hartkodiert.")
        elif any(key in targets for key in ("token", "secret", "api_key", "password")):
            self._add("BK006", node, f"Geheimnis als Literal an '{targets.strip()}'.")

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            targets = " ".join(_dotted(t).lower() for t in node.targets)
            self._check_secret_literal(node, node.value.value, targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # `API_TOKEN: str = "123456:AA…"` — Audit H-1/C: früher unbemerkt,
        # weil nur ast.Assign geprüft wurde.
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self._check_secret_literal(node, node.value.value, _dotted(node.target).lower())
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Jedes tokenförmige Literal überall (Dict-Werte, Call-Argumente,
        # Listen, Rückgaben) — Assignment-Formen können nicht einzeln
        # abgedeckt werden, das Literal selbst ist das Signal (H-1/C).
        if isinstance(node.value, str) and _TOKEN_EMBEDDED.search(node.value):
            self._add("BK006", node, "Token-förmiges Literal im Quelltext.")
        self.generic_visit(node)

    # -- Aufrufe ------------------------------------------------------------
    def _resolve_call_name(self, raw_name: str) -> str:
        """Bildet Aliases/from-Imports auf echte Modulpfade ab (Audit H-1).

        ``rq.get`` → ``requests.get``; ``system(...)`` mit
        ``from os import system`` → ``os.system``; ``s.post`` mit
        ``s = requests.Session()`` → ``requests.post``.
        """
        if not raw_name:
            return ""
        head, _, rest = raw_name.partition(".")
        mapped = self.facts.name_paths.get(head) or self.facts.root_aliases.get(head)
        if mapped is None:
            if "." not in raw_name and head in self.facts.name_paths:
                return self.facts.name_paths[head]
            return raw_name
        return f"{mapped}.{rest}" if rest else mapped

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve_call_name(_dotted(node.func))
        short = name.split(".")[-1] if name else ""
        root = name.split(".")[0] if name else ""

        # Dynamische Ausführung / Deserialisierung
        if name.startswith("pickle.") or name.startswith("dill.") or name.startswith("marshal."):
            self._add("BK003", node, f"Deserialisierung via '{name}' ist untersagt.")
        elif short in DYNAMIC_EXEC_CALLS and not name.count("."):
            self._add("BK003", node, f"Aufruf von '{name}' ist untersagt.")
        elif short in DYNAMIC_EXEC_CALLS and root in {"builtins", "importlib"}:
            self._add("BK003", node, f"Deserialisierung/Exec über '{name}' ist untersagt.")

        # Indirekter Dispatch via getattr(obj, "name") — R-2: Der Zielname
        # steht im zweiten Argument, der Namespace im ersten; der normale
        # Namen-Match (`_dotted`) sieht ein verschachteltes Call-Objekt nicht.
        if isinstance(node.func, ast.Call):
            inner = node.func
            if self._resolve_call_name(_dotted(inner.func)) == "getattr" and len(inner.args) >= 2:
                obj = _dotted(inner.args[0])
                attr = inner.args[1]
                if isinstance(attr, ast.Constant) and isinstance(attr.value, str) and obj:
                    full = f"{obj}.{attr.value}"
                    obj_root = obj.split(".")[0]
                    if attr.value in SHELL_CALLS and obj_root in {"os", "subprocess"}:
                        self._add("BK007", node, f"Indirekter Shell-Aufruf '{full}' ist untersagt.")
                    if attr.value in DYNAMIC_EXEC_CALLS and obj_root in {
                        "builtins", "__builtins__", "importlib",
                    }:
                        self._add("BK003", node, f"Indirekte Code-Ausführung '{full}' ist untersagt.")

        # Shell / Prozesse
        if root in {"os", "subprocess"} and short in SHELL_CALLS:
            self._add("BK007", node, f"Prozess-/Shell-Aufruf '{name}' ist untersagt.")

        # Persistenz
        if short in PERSISTENCE_CALLS and not name.startswith(("audit", "logging")):
            if short in {"connect"} and root != "sqlite3":
                pass  # connect() allein ist kein Verstoß (z. B. signals)
            else:
                self._add("BK002", node, f"Schreibzugriff via '{name}' ist untersagt.")
        if short == "open":
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(flag in mode for flag in ("w", "a", "x")):
                self._add("BK002", node, f"Datei wird zum Schreiben geöffnet (mode='{mode}').")

        # os.open/io.open mit Schreib-Flags sowie os.write/os.pwrite — R-2:
        # Persistenz über Datei-Deskriptoren statt Dateinamen. Der Mode-Check
        # oben sieht nur builtins.open, nicht die os/io-Varianten.
        if short == "open" and root in {"os", "posix", "io"}:
            flags: ast.AST | None = None
            if len(node.args) > 1:
                flags = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == "flags":
                        flags = kw.value
                        break
            if flags is not None and _has_write_flags(flags):
                self._add("BK002", node, f"'{name}' öffnet eine Datei zum Schreiben.")
        if short in {"write", "pwrite"} and root in {"os", "posix"}:
            self._add("BK002", node, f"Schreibzugriff via '{name}' ist untersagt.")

        # Netzwerk-Server
        if name in {"socket.socket", "socket.create_server"} or short == "serve_forever":
            self._add("BK008", node, f"Eigener Socket-Server ('{name}') ist untersagt.")

        # HTTP-Aufrufe: Ziel-Host prüfen (nach Alias-Auflösung, H-1)
        if short in HTTP_CALL_NAMES and root in HTTP_MODULE_ROOTS:
            self._check_http_target(node, name)

        # Logging / Ausgaben mit Inhalten (Root case-insensitiv, seit v2.11.1:
        # `LOGGER = logging.getLogger(...)` ist die übliche Konvention und
        # entging der Regel zuvor vollständig).
        if short in LOG_METHODS and root.lower() in {"logging", "logger", "log", "self", ""}:
            self._check_log_args(node, name)
        if short == "print" and _names_in(node) & SENSITIVE_LOG_NAMES:
            self._add("BK011", node, "print() gibt potenziell Inhalte aus.")

        # Unsicherer Zufall
        if root == "random":
            self._add("BK012", node, "random statt secrets für sicherheitsrelevante Werte.")

        self.generic_visit(node)

    # -- Ziel-URL-Auflösung ---------------------------------------------------
    def _fold_string(self, expr: ast.expr) -> str | None:
        """Löst einfache String-Ziele auf: Literal, Modul-Konstante, f-String-Kopf."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name) and expr.id in self.facts.str_consts:
            return self.facts.str_consts[expr.id]
        if isinstance(expr, ast.JoinedStr):
            # f"api.telegram.org/bot{token}/x" → konstanter Präfix reicht
            # zur Host-Bestimmung. Bekannte Namen (Modul-Konstanten) werden
            # mitgefaltet; der erste dynamische Teil stoppt (R-2) — sonst
            # None (nicht prüfbar → BK010, Blocker).
            prefix: list[str] = []
            for part in expr.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    prefix.append(part.value)
                elif isinstance(part, ast.FormattedValue):
                    folded = self._fold_string(part.value)
                    if folded is None:
                        break
                    prefix.append(folded)
                else:
                    break
            return "".join(prefix) if prefix else None
        return None

    def _check_http_target(self, node: ast.Call, name: str) -> None:
        """Nur api.telegram.org darf per HTTP kontaktiert werden.

        Audit H-1: Vorher zählten nur Literal-Argumente — eine URL in einer
        Variable oder einem f-String hebelte BK004 vollständig aus. Jetzt
        werden Modul-Konstanten gefaltet und f-String-Köpfe ausgewertet;
        wirklich unauflösbare Ziele sind BK010 — seit v2.4.0 ein BLOCKER
        (R-2): ein nicht prüfbares Ziel ist nicht „sicher“, sondern nur
        nicht verifizierbar.
        """
        target = None
        if node.args:
            target = self._fold_string(node.args[0])
        if target is None:
            for kw in node.keywords:
                if kw.arg == "url":
                    target = self._fold_string(kw.value)
                    break
        if target is None:
            self._add("BK010", node, f"Ziel-URL von '{name}' ist nicht statisch prüfbar.")
            return
        host = urlparse(target).hostname or ""
        if not host:
            # Konstanter Präfix ohne Host (z. B. f"https://{var}/…") oder
            # relatives Ziel: prüfbar nur, wenn der Host Teil des Präfix war.
            if "://" in target:
                self._add("BK010", node, f"Ziel-URL von '{name}' hat keinen erkennbaren Host.")
            else:
                self._add("BK010", node, f"Ziel-URL von '{name}' ist nicht vollständig auflösbar.")
            return
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HTTP_HOSTS):
            self._add("BK004", node, f"Ausgehender Aufruf an '{host}' ist nicht erlaubt.")

    def _check_log_args(self, node: ast.Call, name: str) -> None:
        """Inhalte dürfen nicht in Logzeilen landen."""
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                continue
            if _names_in(arg) & SENSITIVE_LOG_NAMES:
                self._add("BK005", node, f"Log-Aufruf '{name}' enthält potenziell Inhalte.")
                return


def analyze_code(source: str, *, filename: str = "<string>") -> ReviewReport:
    """Führt die statische Analyse auf einem Quelltext aus (testfreundlich).

    Bewusst Heuristik, kein Sandboxing: die Regeln fangen die dokumentierten
    typischen Verstöße (auch gegen Alias-/Konstanten-Tricks aus Audit H-1),
    die menschliche Abnahme bleibt zweite Pflichtebene.
    """
    tree = ast.parse(source, filename=filename)
    facts = collect_facts(tree)
    visitor = _SecurityVisitor(filename, source, facts)
    visitor.visit(tree)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    findings = sorted(visitor.findings, key=lambda f: (f.line, f.rule_id))
    return ReviewReport(file=filename, source_sha256=digest, findings=findings)


def analyze_source(path: str | Path) -> ReviewReport:
    """Führt die statische Analyse auf einer Datei aus."""
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    return analyze_code(source, filename=str(file_path))


# --------------------------------------------------------------------------- #
# Menschliche Abnahme: Checkliste, Ledger, Gate
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CheckItem:
    """Ein Prüfpunkt der Review-Checkliste (erscheint auch im PR-Template)."""

    check_id: str
    question: str
    why: str


CHECKLIST: tuple[CheckItem, ...] = (
    CheckItem("C1", "Token nur über BotToken/Umgebung, nie geloggt oder gespeichert?",
              "Tokens in Logs/Dateien sind der häufigste Leak-Weg."),
    CheckItem("C2", "Kein Schreibzugriff auf Dateisystem, Datenbank oder Cache?",
              "Kernanforderung: keine Nutzerdaten-Persistenz."),
    CheckItem("C3", "Alle Eingaben validiert (Typ, Länge, Format, chat_id, Callback-Daten)?",
              "Ungeprüfte Eingaben landen sonst 1:1 im API-Payload."),
    CheckItem("C4", "Nur erlaubte Telegram-Methoden, keine Fremd-APIs?",
              "Begrenzt die Angriffsfläche und den Datenabfluss."),
    CheckItem("C5", "Fehler klassifiziert geloggt — ohne Inhalte, Token oder chat_id?",
              "Fehlermeldungen sind der zweithäufigste Leak-Weg."),
    CheckItem("C6", "Rate-Limits/429 mit Backoff behandelt, Retry-Budget begrenzt?",
              "Verhindert Bot-Sperren und Endlosschleifen."),
    CheckItem("C7", "Session-Ende räumt auf (deleteWebhook, drop_pending_updates, kein Offset)?",
              "Nach der Session darf kein Zustand bei Telegram bleiben."),
    CheckItem("C8", "Keine neuen Abhängigkeiten ohne Begründung, Versionen gepinnt?",
              "Supply-Chain-Risiko; Reproduzierbarkeit."),
    CheckItem("C9", "Tests für Konvertierung, Fehlerpfad und „keine Persistenz\" vorhanden?",
              "Review ist nur so gut wie seine Regressionstests."),
)

CHECKLIST_IDS = frozenset(item.check_id for item in CHECKLIST)


class ReviewError(RuntimeError):
    """Fehler im Review-Prozess (unvollständige Checkliste, Doppelreview …)."""


class ReviewGateError(RuntimeError):
    """Deployment-Blockade: Bot ist nicht freigegeben oder Code hat Blocker."""


class ReviewRole(str, Enum):
    """Rolle der reviewenden Person (für das Vier-Augen-Prinzip)."""

    CONTRIBUTOR = "contributor"
    MAINTAINER = "maintainer"


@dataclass(frozen=True)
class Reviewer:
    handle: str
    role: ReviewRole = ReviewRole.CONTRIBUTOR

    def __post_init__(self) -> None:
        if not self.handle or len(self.handle) > 64 or not self.handle.replace("-", "").replace(".", "").isalnum():
            raise ReviewError("Reviewer-Handle muss alphanumerisch sein (max. 64 Zeichen).")


@dataclass(frozen=True)
class ReviewDecision:
    reviewer: Reviewer
    approved: bool
    checks: tuple[str, ...]
    note: str
    decided_at: float
    source_sha256: str


@dataclass
class ReviewTicket:
    """Ein Review-Vorgang für (Bot, Code-Prüfsumme)."""

    ticket_id: str
    bot_id: int
    source_sha256: str
    submitted_at: float
    file: str = ""
    static_findings: tuple[str, ...] = ()
    decisions: list[ReviewDecision] = field(default_factory=list)

    @property
    def approvals(self) -> list[ReviewDecision]:
        return [d for d in self.decisions if d.approved]

    @property
    def rejections(self) -> list[ReviewDecision]:
        return [d for d in self.decisions if not d.approved]

    def is_approved(self, *, min_approvals: int, require_maintainer: bool) -> bool:
        """Vier-Augen-Prinzip: genug Freigaben, davon mindestens eine Maintainer.

        Audit B-3: Eine einzige Ablehnung invalidiert den Ticket-Stand —
        unabhängig davon, wie viele Freigaben bereits eingetragen waren.
        Der Aufhebungspfad ist ein *neues* Ticket nach Codeänderung
        (:meth:`ReviewLedger.submit` legt bei abgelehntem Vorgang eines an).
        """
        if self.rejections:
            return False
        approvals = self.approvals
        if len(approvals) < min_approvals:
            return False
        if len({d.reviewer.handle for d in approvals}) < min_approvals:
            return False  # dieselbe Person darf nicht doppelt zählen
        if require_maintainer and not any(
            d.reviewer.role is ReviewRole.MAINTAINER for d in approvals
        ):
            return False
        return True


class ReviewLedger:
    """
    Append-only-Vorgangsspeicher für Review-Entscheidungen (nur Metadaten).

    Gespeichert werden Ticket-ID, Bot-ID, Code-Prüfsumme, Regel-IDs der
    statischen Analyse und Entscheidungen. Keine Quelltexte, keine Inhalte.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._tickets: dict[str, ReviewTicket] = {}

    @staticmethod
    def _new_ticket_id() -> str:
        return f"RV-{secrets.token_hex(4).upper()}"

    def submit(self, bot_id: int, source_sha256: str, *, file: str = "",
               static_findings: Iterable[str] = ()) -> ReviewTicket:
        """Legt ein Ticket an (idempotent pro Bot+Prüfsumme).

        Ausnahme (Audit B-3): War das bisherige Ticket zu dieser Prüfsumme
        abgelehnt, entsteht ein **neues** Ticket — eine einmal ausgesprochene
        Ablehnung kann nicht durch weitere approve-Aufrufe überstimmt werden,
        der Bot muss erneut vollständig freigegeben werden.
        """
        existing = self.ticket_for(bot_id, source_sha256)
        if existing is not None and not existing.rejections:
            return existing
        ticket = ReviewTicket(
            ticket_id=self._new_ticket_id(),
            bot_id=int(bot_id),
            source_sha256=source_sha256,
            submitted_at=self._clock(),
            file=file,
            static_findings=tuple(static_findings),
        )
        self._tickets[ticket.ticket_id] = ticket
        audit(LOGGER, logging.INFO, "review.submitted", ticket=ticket.ticket_id, bot=bot_id,
              source=source_sha256[:12])
        return ticket

    def decide(self, ticket_id: str, decision: ReviewDecision) -> ReviewTicket:
        """Trägt eine Entscheidung ein (validiert: Ticket, Prüfsumme, Duplikate, Checkliste)."""
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise ReviewError(f"Unbekanntes Review-Ticket {ticket_id}.")
        if decision.source_sha256 != ticket.source_sha256:
            raise ReviewError(
                "Entscheidung bezieht sich auf eine andere Code-Fassung "
                f"({decision.source_sha256[:12]} != {ticket.source_sha256[:12]})."
            )
        if any(d.reviewer.handle == decision.reviewer.handle for d in ticket.decisions):
            raise ReviewError(f"{decision.reviewer.handle} hat dieses Ticket bereits bewertet.")
        if decision.approved:
            missing = CHECKLIST_IDS - set(decision.checks)
            if missing:
                raise ReviewError(
                    "Checkliste unvollständig: " + ", ".join(sorted(missing))
                )
            unknown = set(decision.checks) - CHECKLIST_IDS
            if unknown:
                raise ReviewError("Unbekannte Check-IDs: " + ", ".join(sorted(unknown)))
        ticket.decisions.append(decision)
        audit(LOGGER, logging.INFO, "review.decided", ticket=ticket_id, bot=ticket.bot_id,
              reviewer=decision.reviewer.handle, role=decision.reviewer.role.value,
              approved=decision.approved)
        return ticket

    def ticket(self, ticket_id: str) -> ReviewTicket | None:
        return self._tickets.get(ticket_id)

    def ticket_for(self, bot_id: int, source_sha256: str) -> ReviewTicket | None:
        """Neuestes Ticket zu (Bot, Prüfsumme) — nach Re-Submit zählt dieses."""
        found: ReviewTicket | None = None
        for ticket in self._tickets.values():  # Einfügereihenfolge = chronologisch
            if ticket.bot_id == int(bot_id) and ticket.source_sha256 == source_sha256:
                found = ticket
        return found

    # ------------------------------------------------------- Audit-Trail (Datei)
    def save(self, path: str | Path) -> None:
        """
        Schreibt den Audit-Trail als JSON.

        Erlaubt, weil hier **keine Nutzdaten** stehen: Ticket-IDs, Bot-IDs,
        Code-Prüfsummen, Regel-IDs und Entscheidungen. Damit kann ein Review
        (z. B. über einen PR) nachvollziehbar dokumentiert werden, ohne
        Inhalte oder Tokens zu berühren.
        """
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(self.audit_trail(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            raise ReviewError(
                f"Audit-Trail '{path}' ist nicht schreibbar ({exc.__class__.__name__})."
            ) from None
        audit(LOGGER, logging.INFO, "review.saved", file=str(target), tickets=len(self._tickets))

    @classmethod
    def load(cls, path: str | Path, *, clock: Callable[[], float] = time.time) -> ReviewLedger:
        """Liest einen Audit-Trail wieder ein (z. B. im CI nach dem Checkout).

        Ein korrupter Trail meldet sich als :class:`ReviewError` — nicht als
        roher ``JSONDecodeError``/``KeyError``-Traceback (seit v2.11.1).
        """
        ledger = cls(clock=clock)
        source = Path(path)
        if not source.exists():
            return ledger
        try:
            entries = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # JSONDecodeError ⊂ ValueError
            raise ReviewError(
                f"Audit-Trail '{path}' ist beschädigt ({exc.__class__.__name__})."
            ) from None
        if not isinstance(entries, list):
            raise ReviewError(f"Audit-Trail '{path}' ist beschädigt (keine Ticket-Liste).")
        for entry in entries:
            try:
                ticket = ReviewTicket(
                    ticket_id=str(entry["ticket_id"]),
                    bot_id=int(entry["bot_id"]),
                    source_sha256=str(entry["source_sha256"]),
                    submitted_at=float(entry["submitted_at"]),
                    file=str(entry.get("file", "")),
                    static_findings=tuple(entry.get("static_findings", ())),
                )
                ticket.decisions = [
                    ReviewDecision(
                        reviewer=Reviewer(str(d["reviewer"]), ReviewRole(str(d["role"]))),
                        approved=bool(d["approved"]),
                        checks=tuple(d.get("checks", ())),
                        note=str(d.get("note", "")),
                        decided_at=float(d["decided_at"]),
                        source_sha256=str(entry["source_sha256"]),
                    )
                    for d in entry.get("decisions", [])
                ]
            except ReviewError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise ReviewError(
                    f"Audit-Trail '{path}' ist beschädigt ({exc.__class__.__name__})."
                ) from None
            ledger._tickets[ticket.ticket_id] = ticket
        return ledger

    def audit_trail(self) -> list[dict[str, object]]:
        """Metadaten-Export für Compliance/Reporting (ohne Inhalte)."""
        trail: list[dict[str, object]] = []
        for ticket in self._tickets.values():
            trail.append(
                {
                    "ticket_id": ticket.ticket_id,
                    "bot_id": ticket.bot_id,
                    "source_sha256": ticket.source_sha256,
                    "submitted_at": round(ticket.submitted_at, 3),
                    "static_findings": list(ticket.static_findings),
                    "decisions": [
                        {
                            "reviewer": d.reviewer.handle,
                            "role": d.reviewer.role.value,
                            "approved": d.approved,
                            "checks": list(d.checks),
                            "note": d.note,
                            "decided_at": round(d.decided_at, 3),
                        }
                        for d in ticket.decisions
                    ],
                }
            )
        return trail


class ReviewGate:
    """
    Verbindet statische Analyse, Checkliste und Registry-Freigabe.

    Nutzung::

        gate = ReviewGate(ReviewLedger(), registry, min_approvals=2)
        ticket = gate.submit(bot_id, "examples/own_bot/minimal_bot.py")   # Statik + Ticket
        gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=("C1", …))
        gate.approve(ticket.ticket_id, Reviewer("bob"), checks=…)
        gate.verify(bot_id, "examples/own_bot/minimal_bot.py")            # vor jeder Session
    """

    def __init__(
        self,
        ledger: ReviewLedger,
        registry: BotRegistry | None = None,
        *,
        min_approvals: int = 2,
        require_maintainer: bool = True,
    ) -> None:
        # ``registry=None`` = lokaler/Git-Modus: das Ledger (Audit-Trail) ist
        # autoritativ, Registry-Prüfungen entfallen. Im gehosteten Betrieb
        # ist die Registry immer gesetzt.
        self._ledger = ledger
        self._registry = registry
        self.min_approvals = max(1, int(min_approvals))
        self.require_maintainer = require_maintainer

    # -------------------------------------------------------------- Analyse
    def analyze(self, path: str | Path) -> ReviewReport:
        """Statische Analyse ohne Nebenwirkungen (Report, kein Raise)."""
        return analyze_source(path)

    def submit(self, bot_id: int, path: str | Path) -> ReviewTicket:
        """
        Reicht eine Bot-Datei zum Review ein.

        Blocker der statischen Analyse stoppen den Vorgang sofort — es gibt
        keinen „Trotzdem freigeben"-Pfad (stattdessen gezielte
        ``# botkit:allow``-Suppressions mit Begründung im Code).
        """
        report = self.analyze(path)
        if not report.ok:
            raise ReviewGateError(report.as_text())
        return self._ledger.submit(
            bot_id,
            report.source_sha256,
            file=str(path),
            static_findings=[f"{f.rule_id}@{f.line}" for f in report.findings],
        )

    # ----------------------------------------------------------- Entscheidungen
    def approve(
        self,
        ticket_id: str,
        reviewer: Reviewer,
        *,
        checks: Sequence[str],
        note: str = "",
    ) -> ReviewTicket:
        """Trägt eine Freigabe ein; erreicht das Ticket die Schwelle, wird der Bot aktiviert."""
        ticket = self._require_ticket(ticket_id)
        decision = ReviewDecision(
            reviewer=reviewer,
            approved=True,
            checks=tuple(checks),
            note=note,
            decided_at=time.time(),
            source_sha256=ticket.source_sha256,
        )
        ticket = self._ledger.decide(ticket_id, decision)
        if ticket.is_approved(min_approvals=self.min_approvals, require_maintainer=self.require_maintainer):
            if self._registry is not None:
                self._registry.mark_approved(ticket.bot_id, ticket.source_sha256)
            audit(LOGGER, logging.INFO, "review.approved", ticket=ticket_id, bot=ticket.bot_id,
                  approvals=len(ticket.approvals))
        return ticket

    def reject(self, ticket_id: str, reviewer: Reviewer, *, note: str) -> ReviewTicket:
        """Lehnt ab; eine einzige Ablehnung invalidiert bestehende Freigaben."""
        ticket = self._require_ticket(ticket_id)
        decision = ReviewDecision(
            reviewer=reviewer,
            approved=False,
            checks=(),
            note=note,
            decided_at=time.time(),
            source_sha256=ticket.source_sha256,
        )
        self._ledger.decide(ticket_id, decision)
        if self._registry is not None:
            self._registry.mark_rejected(ticket.bot_id, note[:200])
        return ticket

    # ---------------------------------------------------------------- Verifikation
    def verify(self, bot_id: int, path: str | Path, *, check_registry: bool = True) -> None:
        """
        Prüft unmittelbar vor dem Session-Start, ob Bot *und* Code freigegeben sind.

        :raises ReviewGateError: bei fehlendem Ticket, fehlenden Freigaben,
            abweichender Prüfsumme, nicht freigegebener Registry oder
            nicht lesbarer Code-Datei (statt rohem ``OSError``).
        """
        try:
            sha = source_sha256(path)
        except OSError as exc:
            raise ReviewGateError(
                f"Bot-Code '{path}' ist nicht lesbar ({exc.__class__.__name__})."
            ) from None
        ticket = self._ledger.ticket_for(int(bot_id), sha)
        if ticket is None:
            raise ReviewGateError(
                f"Kein Review-Ticket für Bot {bot_id} mit sha256={sha[:12]}. "
                "Bitte 'botctl review' ausführen."
            )
        if not ticket.is_approved(min_approvals=self.min_approvals, require_maintainer=self.require_maintainer):
            raise ReviewGateError(
                f"Ticket {ticket.ticket_id}: {len(ticket.approvals)}/{self.min_approvals} "
                "Freigaben — Bot noch nicht freigegeben."
            )
        if not check_registry or self._registry is None:
            return
        record = self._registry.get(int(bot_id))
        if record is None or record.approved_source_sha256 != sha:
            raise ReviewGateError(
                f"Bot {bot_id} ist in der Registry nicht für sha256={sha[:12]} freigegeben."
            )
        audit(LOGGER, logging.INFO, "review.verified", bot=bot_id, ticket=ticket.ticket_id,
              source=sha[:12])

    def _require_ticket(self, ticket_id: str) -> ReviewTicket:
        ticket = self._ledger.ticket(ticket_id)
        if ticket is None:
            raise ReviewError(f"Unbekanntes Review-Ticket {ticket_id}.")
        return ticket
