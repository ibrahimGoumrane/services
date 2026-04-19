# Scraper Logic (Current Runtime Behavior)

This document describes the current business logic of the scraper flow after a run starts.

## Goal of the flow

For each input row, the pipeline tries to produce a storable contact record, enrich missing fields, and persist results in batches.

The logic prioritizes preserving rows that contain meaningful information instead of dropping them.

## 1) Run initialization logic

When a run starts:

1. Build counters and status tracking.
2. Read the CSV dataset.
3. Load all reference lists from DB:
   - generic domains
   - generic users
   - known generic MX roots
   - site-builder domains
   - not-visiting domains
4. If this is a resumed job, restore row position and accumulated counters.
5. If web enrichment is enabled, start browser enrichment and apply current filters.

If CSV loading or reference loading fails, the run exits early with an error entry.

## 2) Row eligibility logic

A row is considered eligible if at least one of these exists:

- fullname
- fname
- lname
- company name
- email
- url

If all are missing, the row is skipped immediately.

## 3) Enrichment decision logic per eligible row

For each eligible row, the pipeline applies these decisions:

1. Read mapped/default values.
2. If provided URL is invalid/unreachable, discard that URL.
3. If search is allowed (no user URL provided and Google search not disabled):
   - Company prefetch search: company name + location.
   - If found and valid:
     - reuse found URL for the row when row has no URL
     - optionally stage a separate company record if domain is not already present in DB
4. Person-focused search (fullname + company, without location) may run when person data is still insufficient.
5. Default fallback search runs if website is still missing.
6. If a website exists:
   - try to reuse data from an existing DB contact with same domain
   - scrape website only when key fields are still missing (email/phone/contact/geo)

## 4) Email fallback logic

If no usable email exists after enrichment:

1. Determine fallback domain from website.
2. If no website domain is available, use nodomaine.com.
3. If fallback domain is already known in DB and name fields exist, try a name-based synthetic email:
   - f.lastname@domain
   - otherwise fullname@domain
4. If that is not possible, generate postmaster+id@domain.

If an existing email is already postmaster+... and the domain is known, the same name-based rewrite is attempted.

## 5) MX and classification logic

After final email selection:

1. Classify email as generic/non-generic and user-generic/non-generic.
2. Attempt MX resolution only in the non-generic path.
3. If MX lookup fails, keep the row with discovered/synthetic email (do not auto-drop solely because MX failed).
4. If MX root is found, apply secondary generic check based on known generic MX roots.

## 6) Skip logic (important)

A row is counted as skipped only when no main contact tuple is returned.

When that happens, the scraper updates the generic skip counters and then uses the current reporting labels below:

1. no_required_field:
   - none of fullname/fname/lname/name/email existed
2. invalid_mx bucket:
   - row failed and CSV email was present
   - this is a broad reporting label, not a strict MX verdict
3. no_email_found:
   - row failed and CSV email was absent

These bucket names are the current counters used by the scraper. They are useful for reporting, but they should not be read as exact root-cause diagnostics.

## 7) Batch persistence logic

Rows are not written one-by-one. They are buffered and flushed at boundaries:

1. Insert newly discovered MX records.
2. Upsert contact records (main + any staged company records).
3. Update job checkpoint for resumability.
4. Perform browser maintenance at boundaries:
   - tab cleanup each boundary
   - periodic browser restart every fixed number of batches (unless a health restart already happened)
5. Reload reference lists from DB and refresh active filters.

## 8) End-of-run logic

At completion (or controlled stop):

1. Clean job flags.
2. Close browser resources.
3. Finalize progress state.
4. Flush logs.
5. Return final counters/statistics.

## 9) Practical outcomes

By design, current logic tends to preserve informative rows:

- MX failure alone does not force drop.
- Missing email can still result in storable synthetic email.
- Company prefetch can create additional staged records.
- Resume/pause/cancel semantics are respected at safe checkpoints.
