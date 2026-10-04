# Search-Jobs — the owner's personal business-development system

Owner: Mohamed Fawzy Labib (GitHub `coolman1984`). Not technical. Replies to him: simple Egyptian Arabic, in this order:
**الخلاصة ← اللي اتعمل ← محتاج منك (القرار والافتراض بين قوسين) ← الخطوة الجاية.**

Purpose: every day, find real opportunities (jobs, freelance, direct clients), score them, study the client, find ready-made
solutions, write tailored drafts, and follow up until a contract. Quality over quantity.

## Red rules (no exceptions, enforced in code where possible)
1. **Nothing is sent in the owner's name without his approval of that exact message.** Approval = a web-flow-signed GitHub
   commit by the owner adding `approvals/<id>.approve` with the draft's SHA-256 (see `docs/BUILD_PLAN.md`). An agent can never
   create or fake an approval. Gmail: drafts only — never `send_message`, `reply` or `forward`.
2. No scraping or automation on platforms that forbid it (red list in `sources/SOURCES.md`). No bypassing blocks, CAPTCHAs or
   rate limits. Never log in with the owner's accounts.
3. No impersonation, no invented experience, results or numbers. Every claim in a draft must trace to `profile/PROFILE.md`.
4. No keys or passwords in the repository, ever.
5. Personal data: only business contact details published publicly for business. Never sell or share data.
6. Never mention the owner's current employer or its systems in any client-facing text without his approval. Never use an
   excluded repository (PROFILE.md §4c) as evidence. Reject opportunities from the employer, its competitors or suppliers.
7. WhatsApp: the owner's number appears only as a wa.me click-to-chat link in his own materials. This system never sends
   or automates WhatsApp messages (business-initiated messages need the person's prior opt-in). Use a separate business
   number, never the personal one, for any future WhatsApp Cloud API setup.
8. Every run writes a short log in `logs/`; every system change gets an entry in `DEVELOPMENT_HISTORY.md` (what, why, lessons).
9. Every run adds at least one idea of its own to `IDEAS.md`.

## Engine (python3 -m engine …)
Stdlib-only Python + SQLite (`data/`, cache) + JSON in `state/` (the record, committed; private fields stay out of git
while the repository is public - see engine/privacy.py). Config in `config/*.toml`. Routines: `docs/ROUTINES.md`.
Tests: `python3 -m unittest discover -s tests -t .` (offline, ~4 s). `.claude/settings.json` + `.claude/hooks/guard.py`
deny e-mail sending and GitHub API writes, and block any write into `approvals/`.
Never pass `trusted=`/`keyring=` to `engine.approval.verify` outside tests.

## Where things are
`profile/` who he is, services, CVs · `portfolio/` public page and case studies · `market/` segments and prices ·
`sources/` source register · `docs/` plan, routines, setup · `solutions/` one folder per pursued opportunity · `logs/` run logs.

## Working rules
- Develop on the session branch; never push to `main` or force-push.
- Test before every push; a regression test for every bug.
- When unsure, pick the safest assumption, write it in `DEVELOPMENT_HISTORY.md`, and continue.
