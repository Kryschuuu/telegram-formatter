# Security Policy

## Berichten einer Schwachstelle

Bitte melde Sicherheitsprobleme **nicht** über öffentliche Issues.

- **Privat:** nutze GitHubs „Report a vulnerability“ (Security-Tab dieses
  Repositories) oder wende dich an die Maintainer über
  [GitHub @Kryschuuu](https://github.com/Kryschuuu).
- **Enthalten ist nie:** echte Bot-Tokens, Nachrichteninhalte oder Chat-IDs —
  beschreibe die Schwachstelle, hänge reproduzierbaren, synthetischen Code an.

## Was gilt als Schwachstelle in diesem Projekt?

Bereiche mit besonderen Anforderungen sind in
[`security/README.md`](../security/README.md) dokumentiert. Insbesondere:

1. Token-/Inhalts-Leaks (Logs, Fehlermeldungen, Persistenz, Tracebacks),
2. Umgehung des Review-Gates (`botctl`) oder des Vier-Augen-Prinzips,
3. Manipulation des Audit-Trails `audit/reviews.json`,
4. Injection in Telegram-Payloads (`chat_id`, HTML-Escaping, LaTeX-Parsing
   mit Seiteneffekten),
5. Supply-Chain: ungepinnte/Ausweich-Abhängigkeiten, fehlende CVE-Bereinigung.

## Reaktionszeit & Prozess

- Eingangsbestätigung: ≤ 3 Werktage
- Bewertung & Patch-Kommunikation: ≤ 7 Werktage
- Fix-Strategie: Patch-Release + Changelog-Eintrag; bei Ausnutzung in
  veröffentlichten Versionen Hinweis im Security-Advisory auf GitHub.

## Unterstützte Versionen

| Version | Unterstützt |
|---|---|
| 2.x | ✔ |
| < 2.x | ✖ (bitte vor Meldung auf aktuelle Version aktualisieren) |
