# Sample policies

Three fictional companies with policies that overlap in topic and contradict each other in detail, so
an answer that leaks across companies is easy to spot.

| Topic | Arcadia Software | Bluepeak Logistics | Corvane Health |
|---|---|---|---|
| Eligibility | Grades L1–L5, home > 5 km | All grades, home > 3 km | Rostered shift staff only, any distance |
| Booking cut-off | 4 hours before | 2 hours before | 6:00 PM the previous day |
| Escort for women on drops | after 7:30 PM (8:00 PM in v1) | after 10:00 PM | after 9:00 PM |
| Weekends / holidays | With L2 manager approval | Not provided (INR 500 reimbursement) | Rostered automatically |
| Airport trips | Reporting manager, cap INR 1,500 | Transport Head, cap INR 2,000 | *Not covered* |
| Standard cost cap | INR 900 (800 in v1) | INR 1,000 | INR 600 |
| No-shows | 2 → warning, 3 → 7-day suspension | INR 250 per no-show after the first | 3 → off roster for a month |
| Guest travel | Not allowed | Allowed with HR approval | *Not covered* |
| Service area | 35 km | 30 km | No limit |
| Pickup wait | 5 minutes | 10 minutes | 3 minutes |
| Safe-arrival check | Within 30 minutes | *Not covered* | Within 20 minutes |
| Temporary address change | Up to 15 days | Up to 7 days | *Not covered* |

Corvane's section 9 deliberately contains an instruction aimed at AI assistants. The assistant must
treat it as text, not obey it.

Where these rules come from, and the real policies and templates they were modelled on, is in
[docs/POLICY_DATA.md](../../docs/POLICY_DATA.md).

## Format

Each file is Markdown with a front-matter header (`company_id`, `company_name`, `doc_id`, `title`,
`version`, `effective_from`) followed by numbered `##` and `###` headings. The heading numbers are
what answers cite, so they must be unique within a document.
