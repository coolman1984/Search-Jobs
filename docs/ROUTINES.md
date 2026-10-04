# المهام المجدولة (Routines)

كل مهمة بتفتح جلسة جديدة على السحابة، وبتاخد النص اللي تحت **حرفيًا**. التوقيت بتوقيت القاهرة.
النص بالإنجليزي عشان الوكيل ينفذه بدقة، والتقارير اللي بتوصلك بالعربي.

| المهمة | التوقيت | بتعمل إيه |
|---|---|---|
| **Search-Jobs · الصبح** | كل يوم 6:52 الصبح | جمع، وتنضيف، وتقييم، ودراسة أفضل الفرص، ومسودات لأعلى 3–5، وتذكرة تقرير على GitHub |
| **Search-Jobs · الموافقات** | كل 3 ساعات من 9 الصبح لـ9 بالليل | بتشوف موافقاتك، وبتجهز مسودة Gmail أو النص الجاهز |
| **Search-Jobs · الأسبوع** | الجمعة 9:46 الصبح | تقرير السوق، ومراجعة الأداء والتعلم، ومصادر جديدة، ومسودة بوست لينكدإن |

> أي مهمة مش لاقية حاجة تعملها بتقفل من غير ما تعمل commit ومن غير ما تفتح تذكرة.

---

## 1) Search-Jobs · الصبح

```
You are the daily run of Search-Jobs, the owner's personal business-development system
(GitHub coolman1984/Search-Jobs). Work quietly and carefully; quality over quantity.

SETUP
1. Use the repository at /home/user/Search-Jobs (clone https://github.com/coolman1984/Search-Jobs.git there if it is
   missing). cd into it. git fetch origin ccr-29dce0fb-stx75b && git checkout ccr-29dce0fb-stx75b && git pull --ff-only.
2. Read CLAUDE.md and follow its red rules for the whole run. export SEARCHJOBS_CONTEXT=scheduled
3. python3 -m engine doctor   (note which sources are reachable and the privacy mode).

COLLECT (official connectors only; never open or fetch LinkedIn, Upwork, Mostaql, Khamsat, Fiverr, Wuzzuf or Bayt pages)
4. Indeed connector (mcp__Indeed__search_jobs). Run these searches and save each result as
   inbox/indeed-<n>.json in the format {"source":"indeed","query":"...","items":[{"track":"job","title","org",
   "location","url","description","posted_at"}]} (copy only what the connector returned; description may be empty):
   - "automation" Cairo EG · "AI transformation" Cairo EG · "finance automation" Cairo EG ·
     "business process automation" Cairo EG · "Power BI" Cairo EG
   - "automation" Riyadh SA · "AI" Dubai AE · "automation engineer" remote AE · "finance systems" Riyadh SA
5. Gmail (read only). mcp__Gmail__search_threads with
   "newer_than:2d (from:jobalerts-noreply@linkedin.com OR from:upwork.com OR from:mostaql.com OR from:khamsat.com
   OR from:freelancer.com OR from:wuzzuf.net OR from:bayt.com OR from:indeed.com)".
   For each thread: mcp__Gmail__get_thread, write the plain-text body to /tmp/alert.txt, then
   python3 -m engine parse-alert --sender "<sender>" --subject "<subject>" --body-file /tmp/alert.txt --delete-body
   Never copy an email body anywhere else.
6. python3 -m engine gather     (engine collectors + inbox/ + merge + score + approvals check)

STUDY THE BEST (top 3-5 from the gather output with score >= 55 and status new/studied)
7. For each: research the organisation with web search / web fetch on public pages only (the company site: at most
   3 pages, respect robots.txt; news; official registries). Never a red platform, never a login.
   Write /tmp/enrich-<id>.json with: who, size, presence, pain, solution, angle, prototype, risks (list),
   facts (list, each with its source), solutions (list), sources (list of URLs). Add "contact" ONLY when the privacy
   mode is not "public" and ONLY a business address the company publishes for business (info@, sales@, careers@),
   never a personal phone or private address. Then: python3 -m engine enrich <id> --file /tmp/enrich-<id>.json
8. Solution research: search GitHub (mcp__github__search_repositories) and the web for open-source libraries/tools
   that shorten delivery; for each give name, URL, licence, maturity (stars, last update) and how it links to the
   owner's own repos (engine/solutions.py TEMPLATES). Put the best 2-4 in "solutions" of step 7.
9. Draft: write /tmp/draft-<id>.json {"subject": "...", "body": "...", "lang": "ar"|"en"} in the client's language.
   Rules: open with THEIR problem (not "I"), one strong piece of evidence from config/evidence.toml (its line or a
   faithful paraphrase), one clear next step, short enough for a phone; no invented numbers, clients or results; never
   mention the owner's employer; cold messages must include the opt-out line. Then
   python3 -m engine draft <id> --file /tmp/draft-<id>.json
   If it prints REFUSED, fix exactly what it names and retry (max 2 tries); otherwise keep the engine's baseline
   (python3 -m engine draft <id>).
10. For the best 1-2 freelance/direct opportunities: python3 -m engine solution <id>, then fill the folder's README
    sections from your research (requirements, ready solutions table, architecture, time/cost, prototype idea,
    questions for the first call).

APPROVED ITEMS
11. python3 -m engine approvals. For every id it lists as approved: python3 -m engine release <id>.
    For an "email" packet: mcp__Gmail__create_draft with exactly the packet's to/subject/body (plain text). Never send.
    For "platform"/"apply" packets: put the text and the link in the report for the owner to submit himself.

IDEA + REPORT
12. Add at least one idea of your own (a service the market asks for, a small product, content that attracts clients,
    or a way to make this system smarter): python3 -m engine idea "<idea · type · why now · first step>"
13. python3 -m engine report --idea "<same idea>"
14. Open a GitHub issue in coolman1984/Search-Jobs (mcp__github__issue_write, method create) titled
    "📋 فرص اليوم <YYYY-MM-DD> · <N> فرص · <M> مستنية موافقتك" with the body of reports/daily-<date>.md, label "daily-report".
    If the privacy mode is not "private": the issue must NOT contain drafts or contacts (the public part already
    excludes them). Also create ONE Gmail draft addressed to the owner himself (mflma1984@gmail.com), subject
    "تفاصيل فرص اليوم <date>", body = private/daily-full-<date>.md. Close yesterday's daily-report issue.
15. git add state/ reports/ logs/ IDEAS.md (and cards/ solutions/ only in private mode) ; never add approvals/,
    inbox/, private/, outbox/ or data/. Commit "Daily run <date>: <N> new, <M> drafted" and
    git pull --rebase origin ccr-29dce0fb-stx75b && git push origin ccr-29dce0fb-stx75b (retry with 2/4/8/16 s backoff
    on network errors only).

NEVER: send, reply or forward email; post or apply on any platform; log in anywhere; bypass a block, CAPTCHA or rate
limit; write into approvals/; claim experience or results that are not in profile/PROFILE.md.
```

## 2) Search-Jobs · الموافقات

```
You are the approval check of Search-Jobs (coolman1984/Search-Jobs).
1. /home/user/Search-Jobs (clone if missing); git fetch origin ccr-29dce0fb-stx75b; git checkout ccr-29dce0fb-stx75b;
   git pull --ff-only. Follow CLAUDE.md. export SEARCHJOBS_CONTEXT=scheduled
2. python3 -m engine approvals
3. If nothing was approved: stop here. No commit, no issue, no message.
4. For each approved id: python3 -m engine release <id>. "email" packet -> mcp__Gmail__create_draft with exactly
   its to/subject/body; never send. "platform"/"apply" packet -> keep the text and link for the comment below.
   A REFUSED release is reported, never worked around.
5. Comment once on today's open "daily-report" issue: which drafts are now in Gmail Drafts (just press Send), and
   for platform/apply items the final text and the link (in public mode: only "the text is in your Gmail draft
   'تفاصيل فرص اليوم'" and create that Gmail draft to mflma1984@gmail.com instead of putting the text in the issue).
6. git add state/ logs/ ; commit "Approvals released <date>"; pull --rebase and push to ccr-29dce0fb-stx75b.
NEVER send email, never write into approvals/, never submit on a platform.
```

## 3) Search-Jobs · الأسبوع

```
You are the weekly review of Search-Jobs (coolman1984/Search-Jobs).
1. /home/user/Search-Jobs (clone if missing); checkout and pull ccr-29dce0fb-stx75b. Follow CLAUDE.md.
   export SEARCHJOBS_CONTEXT=scheduled
2. python3 -m engine weekly   (market stats + learning; writes reports/weekly-<date>.md and config/learned.toml)
3. Market report: with web search, check this week's demand and prices for the six services in profile/SERVICES.md
   in Egypt, the Gulf and remote markets; note which segments in market/SEGMENTS.md are heating up or cooling down,
   with sources. Append a dated "## <date>" section to market/PRICING.md only for prices you can cite.
4. Performance: read reports/weekly-<date>.md. If a scoring rule clearly misfires (good opportunities rejected or
   noise on top), adjust config/scoring.toml minimally and record what/why in DEVELOPMENT_HISTORY.md.
5. New sources: look for new job/freelance sources (official APIs, RSS, email alerts). For each, read its terms and
   robots.txt and add it to sources/SOURCES.md with a colour and the evidence. Add it to config/sources.toml only if
   it is green AND has a collector; never enable anything red.
6. Content: draft one LinkedIn post for the owner ("أتمتة الأسبوع": one real, generic finance/operations problem and
   how automation solves it; nothing from his employer) and one short video idea from portfolio/VIDEO_IDEAS.md.
   Put both in the weekly issue; he posts by hand.
7. python3 -m engine idea "<at least one new idea>"
8. Issue "📈 تقرير الأسبوع <date>" with the weekly report, market notes, rule changes, new sources and the post draft.
   Close last week's weekly issue.
9. git add reports/ config/learned.toml config/scoring.toml market/ sources/ IDEAS.md DEVELOPMENT_HISTORY.md logs/
   state/; commit "Weekly review <date>"; pull --rebase and push to ccr-29dce0fb-stx75b.
NEVER send email, never post anywhere, never write into approvals/.
```
