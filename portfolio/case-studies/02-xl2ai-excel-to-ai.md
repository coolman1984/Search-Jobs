# Case study: xl2ai — from huge Excel files to answers you can trust

**Problem:** Finance and operations teams live in very large Excel workbooks. AI tools cannot read a 100 MB sheet reliably, and copying data by hand breaks the numbers.

**Solution:** A local, offline preparation layer:
`Excel → verified SQLite → catalog, profile and quality → keys and relations → rules and KPIs → run-to-run changes → compact AI context + safe query tools`
- Two extraction engines with one output contract: Microsoft Excel itself (also opens rights-managed files), or a direct reader that needs no Excel.
- Excel stays the source of truth. The AI never touches the raw file.

**Result that exists:** a 1M-row × 10-column file (57 MB) is extracted *and fully verified* in about 55 seconds; the repository holds about 300 automated tests.

**Fits:** accounting firms, finance departments, factories, anyone whose reporting lives in Excel.
**Repo:** github.com/coolman1984/Office-Automation
