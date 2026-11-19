# Policy ingestion: how a new version replaces the old one, and why it can't half-update

This is the design of the part of the service that loads a policy document and makes it the one that
answers. The README has the short version (design decision 3) and a hands-on walkthrough
([Trying ingestion](../README.md#trying-ingestion-a-new-version-or-a-new-client-company)). This document
covers how the pieces fit, what happens at every point where it can fail, and which test proves each
claim.

Code: `app/policies/` (parser, chunker, scanner, registry, ingestion, seed) and
`app/retrieval/vector_store.py`.

---

## 1. What the brief asks for

> *Ingestion of a policy, including a new version of an existing one. Once a new version is active,
> only it answers. A failed ingestion must not leave the policy half-updated.*

and, under "What good looks like": *"After a policy update, only the new version answers."*

"Half-updated" isn't defined, so I listed every state a failed or interrupted update could leave behind,
and designed so none of them can happen:

| Bad state | What the employee would see |
|---|---|
| **Mixed versions.** Some sections from v1 and some from v2 are searchable together | An answer citing v2 that combines v1's 8:00 PM escort rule with v2's cost cap |
| **Pointer ahead of the data.** The registry says v2, but only half of v2's chunks were written | "The policy doesn't cover this" for rules v2 does have |
| **Old removed before new is in.** v1 deleted, v2 not loaded yet | No policy at all for a while, or for good if the upload failed |
| **Labels don't match text.** Chunks carry v1 text but say v2 (or the reverse) | A citation the employee can't find in the document |
| **Two live versions.** Both v1 and v2 marked active | Either one could answer, unpredictably |

The goal: **at any instant, and after any crash, a company has exactly one complete live version, or no
version at all if it never had one.**

---

## 2. The architecture

```
  POST /v1/policies                         startup (lifespan, before any request is served)
  policy_admin token ─► acting_company      offboard former clients ─► reconcile() ─► seed data/policies/*/v*.md
           │                                                                               │
           └─────────────────────────┬─────────────────────────────────────────────────────┘
                                     ▼
 ┌─────────────────────────── IngestionService.ingest(raw, acting_company) ───────────────────────────┐
 │                                                                                                     │
 │  MarkdownPolicyParser ── whole document validated in memory: front matter, version, numbered        │
 │        │                 sections, SHA-256 checksum. Nothing is written yet.                        │
 │        ▼                                                                                            │
 │  company in document == company in token?  (no ─► 403)                                              │
 │        │                                                                                            │
 │  ══════╪═══════ one lock for all uploads (threading.Lock) ════════════════════════════════════      │
 │        ▼                                                                                            │
 │  same version + same checksum already live? ──yes──► "already active", nothing done                │
 │        │                                                                                            │
 │        ▼                                                                                            │
 │  registry.begin()        ─────────────────────────────────────────────►  ┌───────────────────────┐ │
 │        │   newer than anything ever live? else 409; row = staging         │ SQLite  registry.db   │ │
 │        ▼                                                                  │ policy_versions       │ │
 │  SectionChunker ─► InjectionScanner ─► Embedder (MiniLM)                  │  (company, version,   │ │
 │        │   one chunk per section; id = company:doc:vN:section:part        │   checksum, status)   │ │
 │        ▼                                                                  │ partial unique index: │ │
 │  store.add()  ─► Chroma collection policy_<company>                       │  ≤ 1 active / company │ │
 │        │        chunks tagged {company_id, version}: INVISIBLE,           └───────────────────────┘ │
 │        │        because every read filters on the live version                       ▲             │
 │        ▼                                                                             │             │
 │  registry.activate()  ── ONE transaction: old row → retired, new row → active ───────┘             │
 │        │                 ◄═══ the commit point: the only step users can see ═══►                   │
 │        ▼                                                                                            │
 │  prune: keep the new version + the one just retired, delete older chunks   (best effort, see §7)   │
 │        ▼                                                                                            │
 │  notify listeners: BM25 cache drops that company's indexes                                          │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────┘

 Reading side (every question):
   token ─► company ─► registry.active(company)  read ONCE ─► (company, version) pinned for the request
        ─► Chroma collection policy_<company>, where version = pinned  (+ BM25 index keyed by (company, version))
        ─► ensure_same_tenant(): every chunk's company and version re-checked before it can reach a prompt
```

Two stores are involved, and that's the root of the problem:

- **SQLite registry** (`/data/registry.db`): one row per version with its status. It is the only
  answer to "which version is live?"
- **Chroma** (`/data/chroma`): one collection per company. Every chunk records its company and version.

Both live on the Docker volume `policy_data`, so they survive restarts and rebuilds.

---

## 3. The core idea: write invisibly, then flip one row

SQLite and Chroma can't share a transaction. If the design depended on both being updated together, a
crash between the two writes would leave them disagreeing. So **the design never needs both to change
at once.** Chroma is allowed to hold extra chunks. Only one SQLite row decides what is visible, and
SQLite changes it atomically.

Three rules make this work:

1. **New chunks never touch live ones.** Chunk ids include the version (`yamuna:yamuna-transport-policy:v2:4.2:0`),
   so writing v2 can't overwrite a v1 chunk. Live v1 stays exactly as it was until the switch.
2. **Chroma only ever returns the version it is asked for.** Chroma doesn't decide which chunks count:
   the registry does. Every read passes Chroma a metadata filter `where={"version": <version>}`, both the
   vector search (`ChromaPolicyStore.query`) and the chunk listing the BM25 index is built from
   (`.chunks`). Any other version sitting in the same collection is invisible to that read, whether it's
   v2 half-written before the switch, v1 just retired after it, or leftovers from a crash. Then
   `ensure_same_tenant` rechecks every returned chunk's version, so a wrong filter would be refused,
   not answered from.
3. **The switch is one SQLite transaction** (`PolicyRegistry.activate`, `BEGIN IMMEDIATE`): retire the old
   row and activate the new one, both or neither.

**Why this order, and not another:**

| Alternative | What a failure in the middle leaves |
|---|---|
| Flip the pointer first, then write chunks | Pointer ahead of the data: v2 is live with half its sections |
| Delete v1, then write v2 | No policy, or none at all if the write fails |
| Overwrite v1's chunks in place | Mixed v1 and v2 sections, labels that don't match text |
| **Write v2 invisibly, flip, then prune older versions** (chosen) | At worst, leftover chunks nobody can read, removed by `reconcile()` |

This is the blue/green deployment pattern, or a shadow write followed by a pointer swap, applied to a
document.

---

## 4. Every failure point, step by step

"Error" means an exception inside a running process: the embedder is down, the disk is full, Chroma
raises. "Crash" means the process dies there: `kill -9`, power loss, an out-of-memory kill.

| # | Step | Writes | If it errors | If the process crashes here | What the admin sees |
|---|---|---|---|---|---|
| 1 | Parse and validate | nothing | 422 `invalid_policy`, with the reason | nothing to undo | "could not be read: … Nothing was changed." |
| 2 | Company check | nothing | 403 | nothing to undo | refused |
| 3 | Checksum no-op | nothing | n/a | nothing to undo | 200 `already active` |
| 4 | `registry.begin` | `staging` row | 409 if not newer. The transaction rolls back | SQLite rolls back an unfinished transaction when it next opens the file | "not newer than … Nothing was changed." |
| 5 | Chunk, scan, embed | nothing (memory) | row → `failed`, nothing in Chroma | `staging` row, no chunks: reconcile marks it `failed` | 503 `ingestion_failed`: "the previous version is still the one in use" |
| 6 | `store.add` | v2 chunks, invisible | row → `failed`, the partial chunks deleted | `staging` row plus some or all v2 chunks: reconcile marks it `failed` and deletes them | same 503 |
| 7 | `registry.activate` | the switch | rolled back, row → `failed`, chunks deleted | transaction rolled back on next open, then as in row 6 | same 503 |
| 8 | Prune: keep v2 and v1, delete anything older | removes v0 and older | logged and ignored: no new question asks for those versions | old chunks left over: reconcile deletes them | 200, activated |
| 9 | Notify listeners | in memory | n/a | the BM25 cache is in memory, so it's empty after a restart anyway | 200, activated |

In rows 1 to 7, v1 is live and complete, and every question is answered from it. From row 8 onwards,
v2 is live and complete. **No row has a third state.**

Two details from the code:

- **If the failure handler can't tell what's live, it does nothing.** `_abandon` marks the version failed
  and deletes its chunks only after reading back from the registry that this version is *not* live. If
  that read fails too, say SQLite is unavailable, it leaves the chunks for `reconcile()` rather than risk
  deleting the chunks of a version that did go live.
- **A failed version can be retried with the same number.** `begin()` replaces a `failed` or `staging`
  row for that version. Chunk ids are deterministic, so any leftover chunks are overwritten by the
  upsert, not duplicated.

### Recovery at startup: `reconcile()`

`Container.startup()` runs before the server accepts any request, in this order:

1. **Offboard** companies that are no longer in the employee directory (registry rows first, then their
   Chroma collection).
2. **Reconcile:** every `staging` row is an ingest that never finished, so mark it `failed`. Then, for
   every company collection in Chroma, delete each chunk whose version isn't the live one (all of them
   if nothing is live).
3. **Seed** `data/policies/<company>/v*.md` in version order. This is safe to run again: the live
   version is a no-op, older versions get a 409 that the seeder logs and skips, and one broken file
   doesn't stop the other companies loading.

Reconcile needs no record of what was in progress. The registry says what is live, and anything else in
Chroma is garbage by definition. It runs before any request, so it can't remove chunks a question is
using.

---

## 5. Version lifecycle

```
                    begin()                 activate()                 next version activated
   (upload) ───────────────►  staging  ─────────────────►  active  ─────────────────────────►  retired
                                │   ▲                                                         (never live again:
              error, or crash   │   │  same version uploaded again                             later uploads must
              (via reconcile)   ▼   │  (begin() replaces the row)                              be a higher number)
                               failed
```

- `staging` is never visible. Only `active` is read by questions.
- A **partial unique index**, `one_active_version_per_company ON policy_versions(company_id) WHERE
  status = 'active'`, makes two active versions impossible in the database itself, even if this class
  had a bug.
- "Newer" means higher than the highest version that was ever `active` or `retired`. Failed versions
  don't count, so a failed v3 can be retried as v3.

Why the admin sets the version number, why gaps are allowed and why a reused number is refused is in
README design decision 3.

---

## 6. Durability: what survives a crash or power loss

| Data | Where | Why it survives |
|---|---|---|
| Which version is live | `/data/registry.db` (SQLite) | Default rollback journal with `synchronous=FULL`: once `COMMIT` returns, the switch is on disk. An interrupted transaction is rolled back the next time the file is opened |
| Policy chunks and embeddings | `/data/chroma` (Chroma persistent client) | On disk on the same volume. Correctness doesn't depend on Chroma being atomic: a torn write can only affect a version that isn't live yet, and reconcile removes it |
| BM25 keyword indexes | memory | Rebuilt lazily from Chroma per (company, version). Nothing to recover |
| Source documents | `data/policies/` (seed) or the admin's copy (upload) | The service stores parsed chunks, not the original upload. See limitations |

Both stores live on the named volume `policy_data` (`docker-compose.yml`), so `docker compose down` and a
rebuild keep them. `docker compose down -v` deletes them, and the next start seeds again from
`data/policies/`.

---

## 7. Questions asked while an upload is running

- **One version per answer.** `QuestionAnsweringService._answer` reads `registry.active(company)` once
  and passes that `(company, version)` to retrieval. Every chunk in one answer, and the version in its
  citation, come from that one read, so an upload landing mid-question can't mix v1 and v2 in one reply.
- **The BM25 cache can't serve a stale version.** Indexes are keyed by `(company, version)`, so v2 gets
  a new index whatever happened to v1's. Clearing the cache on activation only frees memory; correctness
  doesn't depend on it.
- **A final check before the prompt.** `ensure_same_tenant` refuses any chunk whose company or version
  isn't the pinned one, and the employee gets "try again". This is on top of the per-company collection
  and the version filter.
### Found and fixed: a question caught mid-switch was told "not covered"

**The bug.** Step 8 used to delete v1's chunks straight after the switch. A question that read "v1 is
live" a few milliseconds before the switch then searched Chroma for v1 after the delete. It found
nothing, fell below the evidence threshold, and the employee was told *"your policy doesn't cover
this"* about a rule the policy does have. It was never a leak or a mixed answer, but it was a wrong
reply. I found it while writing this document and reproduced it: read the pointer (v1), ingest v2,
retrieve for v1, and 0 chunks came back.

**The fix.** Activation now keeps the version it just retired and deletes only versions older than that
(`IngestionService._prune_quietly`, which calls `store.delete_all_except(company, {new, previous})`). v1
is deleted when v3 goes live, so at most one extra version (a few dozen chunks) is kept per company.
`reconcile()` at startup still keeps only the live version: no question is running then, so the
retired one isn't needed.

**Why keeping v1 can't make it answer again.** This is the filter in §3 rule 2. Retrieval never asks
Chroma for "this company's chunks". It asks for "this company's chunks *with version = N*", and N comes
from the registry. After the switch, every new question gets N = 2 from the registry, so Chroma's
`where={"version": 2}` excludes v1 even though v1's chunks are in the same collection. These are the
same rules that kept v2 invisible while it was being written. The only reader that still sees v1 is
the question that pinned v1 *before* the switch, and finishing it from v1 is correct: it gets one
consistent answer from the version that was live when it was asked. Every question asked after the
switch gets v2.

`test_question_pinned_to_old_version_still_finds_it_after_the_switch` reproduces the race. It fails with
the old deletion and passes with the fix. `test_new_version_replaces_old_completely` checks that a new
question retrieves only v2 while v1's chunks are still stored.

---

## 8. Concurrent uploads

All uploads go through one `threading.Lock` in `IngestionService`. Uploads are rare (a few times a year
per company), so one lock is simpler than per-company locks and costs nothing. Without it, two uploads
of the same version could each clean up the other's chunks after a failure.
`test_concurrent_uploads_of_same_version_leave_one_clean_copy` uploads v2 from four threads at once:
one activates, the other three find it already live (same checksum) and do nothing, and exactly one
clean copy of v2 is left.

Parsing happens before the lock, so a large malformed upload is rejected without blocking other
uploads.

---

## 9. What proves it

Every claim above has a test that can be rerun (`docker compose run --rm app pytest tests/test_ingestion.py
tests/test_registry.py tests/test_vector_store.py tests/test_seed.py tests/test_api_policies.py`).
Failures are injected with test doubles: an embedder that fails on a chosen call, and a Chroma store
that writes half a batch and then raises "disk full".

| Claim | Test |
|---|---|
| After an update a new question retrieves only v2 (v2's 7:30 PM, never v1's 8:00 PM), even with v1's chunks still stored | `test_ingestion.py::test_new_version_replaces_old_completely` |
| A question that pinned v1 just before the switch still finds v1 (the race in §7) | `test_question_pinned_to_old_version_still_finds_it_after_the_switch` |
| Only the live and just-retired versions are kept: activating v3 deletes v1 | `test_only_the_live_and_just_retired_versions_are_kept` |
| After a restart only the live version is left | `test_restart_keeps_only_the_live_version` |
| Embedder fails mid-ingest: v1 still live, v2 `failed`, only v1 chunks in Chroma | `test_failed_embedding_keeps_old_version_live` |
| Chroma writes half the batch then fails: v1 still live, no v2 chunks left | `test_crash_halfway_through_writing_chunks_keeps_old_version_live` |
| Process dies after writing chunks, before the switch: restart marks v2 failed and removes its chunks | `test_reconcile_cleans_up_after_a_process_crash` |
| A malformed document writes nothing to either store | `test_bad_document_writes_nothing` |
| A failed version can be retried and then goes live | `test_failed_version_can_be_retried` (ingestion and registry) |
| Re-uploading the live version is a no-op | `test_reingesting_the_live_version_is_a_no_op` |
| Older versions are refused | `test_older_version_is_refused`, `test_registry.py::test_versions_only_move_forward` |
| A staged version isn't live until activated | `test_registry.py::test_staged_version_is_not_live_until_activated` |
| The database itself refuses two active versions | `test_registry.py::test_database_refuses_two_active_versions` |
| A query only ever sees the requested version | `test_vector_store.py::test_query_only_sees_requested_version` |
| An admin can't upload for another company, and the live version is unchanged | `test_api_policies.py::test_admin_cannot_upload_for_another_company` |
| Through the API, a failed ingest says the old version is still in use | `test_api_policies.py::test_failed_ingest_reports_that_old_version_is_kept` |
| Restarting doesn't create new versions | `test_seed.py::test_restart_is_idempotent` |

---

## 10. Limits, and what I'd change for production

- **One process.** The lock is in memory. The SQLite switch is safe across processes (`BEGIN IMMEDIATE`
  serialises writers), but with several workers two processes could still clean up each other's
  chunks. Running more than one would need the lock in the database, for example a lease row per
  company.
- **A question that is slower than two uploads.** The fix in §7 keeps one retired version. A question
  that pinned v1 would only miss it if v2 *and* v3 were both activated before it searched. Each upload
  takes seconds and a search takes milliseconds, so I didn't keep more.
- **Ingestion runs inside the request.** The admin's HTTP call waits for embedding. This takes seconds
  for a 30-page policy on CPU, which is fine at a few uploads a year. A bigger system would queue the job
  and return 202 with a status URL.
- **Missing chunks aren't detected.** Reconcile removes chunks that shouldn't be there, but doesn't
  check that the live version's chunks are all present. If Chroma's files were damaged while
  `registry.db` survived, questions would get a safe "not covered" rather than an answer. Storing the
  chunk count in the registry row and comparing it at startup would catch this.
- **No rollback endpoint.** To go back, re-upload the old text under a new version number. This is
  deliberate: versions only move forward, so the registry history is an audit trail of what was live
  and when.
- **The original document isn't stored.** Only parsed chunks are kept, so a reviewer can't download
  "exactly what the admin uploaded". The checksum is recorded, so a copy can be verified against it.
