# Sample policy data: requirements, research and design

The three sample policies in `data/policies/` are the dataset the assistant answers from. This note
records what the problem statement asks of them, what real-world material they are based on, and how
that material turned into the documents. All three companies are fictional and every number in them
is invented. The research decided *which kinds of clause exist* and *what range a value plausibly
sits in*, not the values themselves.

## 1. What the problem statement requires of the policies

Taken from Case Study B (the policy-related lines only), with where each one is met.

| # | Requirement in the statement | Where it is met |
|---|---|---|
| R1 | "Three short policy documents for three fictional companies" | Yamuna Software Services, Gomti Logistics, Medhavi Health. Each is 1,000 to 1,400 words. |
| R2 | "overlapping terms and deliberately contradictory rules so any cross-company leak shows" | Every company answers the same set of questions (escort time, cut-off, cap, no-show, guests, weekends, airport) with a different value. See the contrast table in section 4. |
| R3 | Policies cover eligibility "by grade, shift or distance from office" | Yamuna: grade (L1–L5) and distance (> 5 km). Gomti: all grades, distance (> 3 km). Medhavi: rostered shift only, any distance. |
| R4 | Night-shift rules, with a women's security escort "after 8 PM at one company, after 10 PM at another" | Yamuna escort after 8:00 PM in v1 (7:30 PM in v2), Gomti after 10:00 PM, Medhavi after 9:00 PM. |
| R5 | Cut-off timings | Yamuna 4 h before, Gomti 2 h before, Medhavi 6:00 PM the previous day. |
| R6 | Cost caps | INR 800 (900 in v2) / 1,000 / 600, plus separate airport caps. |
| R7 | Ad-hoc trip approvals | Reporting manager / site Transport Head / shift supervisor. |
| R8 | Weekend and holiday pickups | Yamuna with L2 approval, Gomti not provided (reimbursement), Medhavi automatic from the roster. |
| R9 | Guest travel | Yamuna not allowed, Gomti with HR approval and host present, Medhavi silent. |
| R10 | No-show penalties | Warning then 7-day suspension / INR 250 per no-show / one month off the roster. |
| R11 | "Policies change a few times a year. Once a new version is live, the old one is dead." | Yamuna has two versions (v1 withdrawn, v2 live). Each document states its version and effective date. |
| R12 | "Some questions have no answer in the policy at all" | Deliberate gaps: Medhavi has no airport or guest rule. Gomti has no safe-arrival check. No company mentions pets, in-cab Wi-Fi or parking. |
| R13 | "A policy document ... may also carry text that tries to steer the assistant" | Medhavi section 9 contains an instruction aimed at AI assistants. |
| R14 | Employees must "see which part of the policy it came from" | Every document uses unique numbered headings (`## 4. Night-shift travel`, `### 4.2 Security escort`) so a citation is a document, version and section. |
| R15 | The employee desk questions: 9:30 PM drop, Saturday cab, airport approver, no-show twice | Each is answerable, with a different answer, at each company (or an honest "not covered"). |
| R16 | "usually a 10 to 30 page document" | The statement asks for *short* documents, so ours are condensed, but each keeps the full clause structure of a real policy (definitions, eligibility, booking, night rules, penalties, safety, review). |

## 2. Research

The question was: what do real Indian corporate transport policies contain, what values do they use,
and what does the law say about night travel for women. Sources are listed with what each one
contributed. Search results were mostly vendor blogs and law-firm summaries, so the legal points are
used only to set *plausible ranges* and are not presented as legal advice.

### 2.1 A real company policy

- **Qualcomm India, Employee Transportation Policy v1.0** (Bangalore, for internal use; a copy is
  publicly hosted at <https://qcommute.falconavl.com/employee/BDCTransportPolicy.pdf>). The most useful
  single source. It shows the real shape of such a document: 14 numbered sections plus annexures.

  | What it shows | How we used it |
  |---|---|
  | Eligibility is regular full-time staff only, with named exceptions approved by the BU head and HR | Yamuna's contractor/intern clause (2.3) |
  | Pickup from nodal points up to 2 km from home, an outer service boundary of about 30 km, and a minimum-use rule for new routes | Coverage limits: Yamuna 35 km, Gomti 30 km. Medhavi deliberately has none. |
  | Registration with a minimum commitment of 3 months, and opt-out cut-off dates | Yamuna registration clause (2.5), 3-month commitment |
  | Cut-offs by request type: night cabs 1 h, ad-hoc 3 h, team events 24 h | Cut-offs differ by request type and by company |
  | No-show: cancel at least 15 minutes ahead, a cab is auto-cancelled after a 15-minute no-show, repeated no-shows go to the manager by email | The pickup wait, the no-show definition, and "copy the reporting manager" |
  | Dark hours 20:00 to 06:00. Escort for women on a first pickup or last drop. | Yamuna's 8:00 PM escort and first-pickup/last-drop rule |
  | A temporary address change allowed up to 15 days with prior approval | Address-change clauses (Yamuna 15 days, Gomti 7 days) |
  | Transport must not be used for families, friends or visitors; ID card mandatory | Guest rules (Yamuna: not allowed) and the ID-card clause |
  | A panic button in each cab, an incident escalation matrix, a monthly route review, a revision history table | Safety and incidents section, review section, version metadata |

### 2.2 Policy templates and practitioner guidance

- **greytHR transport policy template** (<https://www.greythr.com/downloadable-resource/transport-policy>).
  Six-part structure: objective, scope, procedure, special circumstances and exceptions,
  non-compliance. It adds: women on late or night shifts must use company transport, a man is first
  pickup and last drop (otherwise a security person rides along), address changes at least two days
  ahead, no stops or route changes, and deviations need HR approval. We used the exceptions and
  non-compliance structure, and the "women on night shifts use company transport" rule for Medhavi.
- **Cab-rental-hub template guide** (<https://cabrentalhub.in/blog/employee-transport-policy/>). Lists the
  clauses a good policy should have: definitions (shift, roster, no-show, ad-hoc, escort), a home to
  office distance band of 3 km to 35 km, night transport mandatory between 20:00 and 07:00, a 5-minute
  no-show grace period, roster confirmation by 15:00 the day before, an escalation matrix, a review
  clause with a named owner and version number. We used the distance band to choose our 3 km and 5 km
  floors and 30 km and 35 km ceilings, the 5-minute grace period, and the definitions section.
- **WTI Cabs, "design system-enforceable safety SOPs"**
  (<https://www.wticabs.com/knowledge-hub/thought-leadership/safety-compliance-risk/policy-sop-design/index.html>).
  Argues that night windows must be exact and parameterised (not "late night"), that escort rules
  must state who, which time band and what happens when an escort is unavailable, and that every
  policy change needs a version, a rationale and a date. We used this to write explicit escort-
  unavailable fallbacks and to state a night window in hours in every policy.
- **MoveInSync no-show enforcement guide**
  (<https://helpcenter.moveinsync.com/support/solutions/articles/1070000085994-no-show-policy-enforcement-feature-guide>).
  Shows how a client company configures no-show rules: a count within a date range or calendar month,
  a warning count below the penalty count, consecutive counting, direction (login or logout), an
  "alternate transport used" exemption, and automatic or approval-based restoration. This is why our
  no-show sections differ in kind and not just in number: a warning then suspension, a money
  deduction, and removal from the roster, each with its own counting window and restoration rule.
- **Routematic employee transportation policy guide**
  (<https://routematic.com/blog/employee-transportation-policy-in-india/>). Contributed the
  "digital, timestamped safe-drop confirmation, not a phone call" idea and the review triggers
  (annual, and after a safety incident or a state rule change).

### 2.3 Rules and practice on women's night travel

Sources: Nishith Desai Associates, "DEI series part 1: female employees working in night shift"
(<https://nishithdesai.com/research-and-articles/hotline/hr-law-hotline/dei-series-part-1-female-employees-working-in-night-shift-in-india-recent-updates-10628>),
and a Legal60 compliance summary (<https://legal60.com/?p=41859>). Both are secondary summaries of state
rules. What they showed that matters for the dataset:

- The night window is **not a single national number**. It starts at 7 PM (Andhra Pradesh, Himachal
  Pradesh, Uttar Pradesh factories), 8 PM (Karnataka, Tamil Nadu, Haryana), 8:30 PM (Telangana),
  9 PM (Kerala, Madhya Pradesh), 9:30 PM (Maharashtra) or 10 PM (Odisha). This is the real-world
  reason it is sensible for three fictional companies to state three different escort start times
  (8:00 PM, 9:00 PM, 10:00 PM) and for one to move its time earlier in a later version (Yamuna v2).
- Common conditions: written consent from the woman before night work, free transport from home to
  work and back, driver background checks, GPS tracking, a guard or escort in the vehicle, and an
  internal committee under the POSH Act. Haryana allows a woman to opt out of employer transport in
  writing. Telangana adds the rule that a woman is not the first pickup or last drop without a guard.
  These became the consent clause (all three), the opt-out clause (Medhavi, a Haryana site), and the
  first-pickup/last-drop rules.
- Several of the figures in vendor blogs conflict with each other and with the state notifications
  (for example the start of Delhi's night window). Nothing from them is copied as a value.

### 2.4 Hostile or irrelevant text inside documents

The "text that tries to steer the assistant" requirement is a known class of attack (indirect prompt
injection, where instructions arrive in retrieved data rather than from the user). Medhavi's section 9
imitates what appears in practice: a sentence addressed to "AI assistants" inside an otherwise ordinary
section, here placed in "Digital channels" because staff-app or FAQ text is a plausible place for a
pasted note. It tries to override the cost cap, a rule that is easy to check in an answer.

## 3. Design decisions that came out of the research

1. **Real clause structure, invented values.** Each document follows the Qualcomm/template spine:
   scope and definitions, eligibility, booking and cut-offs, night travel, weekends, ad-hoc and airport
   trips, cost, no-shows, guests, safety and incidents, review, contact. A document that only had the
   topics named in the statement would be an unrealistic retrieval target.
2. **Different *kinds* of rule, not only different numbers.** If the three companies differed only by
   a number a retriever could rank them by luck. Eligibility is by grade at one, distance at another,
   shift at the third. No-shows are a warning ladder, a money deduction and a roster removal.
   Weekends are an approval, a reimbursement and an automatic roster.
3. **Deliberate silence.** A policy that answers everything cannot test "the policy does not say".
   Gaps are placed where a model is tempted to borrow from a neighbour: Medhavi says nothing on
   airport trips or guests, Gomti says nothing on safe-arrival checks.
4. **A realistic version change.** Policy updates in practice move a threshold and a cap, not the
   whole document. Yamuna v2 changes exactly three numbers (night window and escort start, trip cap)
   and says so in a "Changes in this version" section, so a stale answer is detectable.
5. **Stable section numbers.** Heading numbers are the citation. They are fixed per topic across
   versions (section 4.2 is always the escort rule) so a verified evaluation case stays valid when a
   policy changes.
6. **Clauses are self-contained.** Each section states its own subject ("the Yamuna cost cap"), which
   helps retrieval and prevents an answer from needing another section.

## 4. The contradictions at a glance

| Topic | Yamuna Software Services (v2) | Gomti Logistics | Medhavi Health |
|---|---|---|---|
| Who is eligible | Grades L1–L5, home > 5 km | Every grade, home > 3 km | Rostered shift staff only |
| Outer limit | 35 km by road | 30 km | None stated |
| Registration | 3-month commitment | None | Automatic from the roster |
| Cut-off | 4 hours | 2 hours | 6:00 PM the day before |
| Cancel without penalty | 2 hours | 1 hour | Same 6:00 PM cut-off |
| Pickup wait | 5 minutes | 10 minutes | 3 minutes |
| Night window | 7:30 PM – 6:00 AM | 10:00 PM – 6:00 AM | 9:00 PM – 6:00 AM |
| Escort for women | After 7:30 PM (8:00 PM in v1) | After 10:00 PM | After 9:00 PM |
| Safe-arrival check | Within 30 minutes | *Not covered* | Within 20 minutes |
| Weekends / holidays | L2 manager approval | Not provided, INR 500 reimbursement | Rostered automatically |
| Ad-hoc approver | Reporting manager | Site Transport Head | Shift supervisor |
| Airport trips | Reporting manager, INR 1,500 | Transport Head, INR 2,000 | *Not covered* |
| Standard cap | INR 900 (800 in v1) | INR 1,000 | INR 600 |
| No-show | 2 → warning, 3 → 7-day suspension | 1st reminder, then INR 250 each | 3 → off roster 1 month |
| Guests | Not allowed | HR approval, host must travel | *Not covered* |
| Temporary address change | Up to 15 days | Up to 7 days | *Not covered* |

## 5. Limits of this research

- The values are invented. No real company's numbers were copied, and the Qualcomm document is used
  as a structural model only.
- The legal summaries are secondary sources that disagree in places and were not checked against the
  gazette. The sample policies are not legal advice and are not claimed to comply with any state's
  rules; a real Hyderabad site, for instance, could not start its escort rule as late as Gomti does.
- The statement describes 10 to 30 page documents. Ours are short on purpose (as required) so they
  keep the clause structure and drop the annexures, route maps and escalation tables.
