You are narrowing a set of ministries down to the parts of them that could fund a subject.

Today is {TODAY}.

The Israeli budget nests four levels deep: ministry (`26`) → **domain** (`26.03`) → program (`26.03.28`) → line (`26.03.28.31`). You are looking at the **domain** level. Whole ministries were selected because they plausibly touch the subject, but most of a ministry has nothing to do with it: the environment ministry funds renewable energy and also `טיפול בפסולת מוצקה`, `ים וחופים`, `חומרים מסוכנים` and `צער בעלי חיים`.

Everything under a domain you `drop` is discarded without being read. Everything you keep open is examined program by program and then line by line, so an unnecessary `keep` costs time but loses nothing.

## Verdicts

* **`keep`** — the domain is about the subject, or an obvious part of it sits here. Not "belongs to the same broad policy field": a ministry was selected because it *might* fund the subject, and most of what it funds does not. `קרן הניקיון` is not green energy because both are environmental; `מניעת זיהום מים` is not green energy because both are environmental. The test is the actual activity, not the field it sits in — waste *disposal* is not energy, while waste-*to*-energy is. If your reason names a category wider than the subject, this is not a `keep`.
* **`ambiguous`** — you cannot tell from the title. This is the right answer for generic administrative containers — `פעילות יחידות המשרד`, `תחום פעולה כללי`, `פעולות`, `שכר ותפעול`, `קרנות`, `מטה` — which reveal nothing and frequently do hold subject spending. It is also right for a domain that is adjacent to the subject without obviously being it.
* **`drop`** — the domain is about something else. You should be able to name what.

Reserves and clearing containers (`רזרבה`, `רזרבות`, `חשבון מעבר`) are `ambiguous`, not `drop`: they are reported separately rather than ignored.

## Calibration

This is a coarse cut, and it is the only stage that can discard a whole branch cheaply, so use it. A ministry usually has 10–25 domains and typically **2–5 of them** relate to any given subject. Expect to `drop` well over half.

Do not hedge everything into `ambiguous` — that returns the whole ministry and defeats the purpose of this pass. Reserve `ambiguous` for titles that are genuinely uninformative or genuinely adjacent, and `drop` the rest with confidence. When a domain's title names a different policy area outright, `drop` it.

## Output

One entry per domain, using the exact `code` given. `reason` is at most eight words. Judge every domain in the input.

---

Subject: **{SUBJECT}**

## Domains

{DOMAINS}
