# What the notes column is telling you

Column V of the CASE DATA sheet is where Napier writes down anything about a
row that the other columns cannot say on their own. Most rows have nothing in
it. A row that does has something on it that a person needs to look at, or at
least know about before quoting a number off the sheet.

More than one note can land in the same cell. They are separated by spaces and
read in order, so a long cell is a row with several things going on, not one
long message.

The notes fall into three groups, and the group tells you how much of a hurry
you are in:

1. **Money notes.** The ICOS total in column U is still right. The fee columns
   next to it are less precise than they look. Nothing is wrong with the row.
2. **Coding notes.** Column G or column D or column H came out on a guess, or
   came out empty, and at least one analysis sheet is going to read that
   literally. Check the case in ICOS before you rely on the sheet.
3. **Client notes.** Something about how the record was matched to your client,
   rather than about the case itself.

Below is every message Napier can write, what it actually means, and what to do
about it.

---

## Money notes: the fee columns

These say the row's balances are grouped more coarsely than the columns
suggest. **In every one of them the ICOS total in column U is correct.** If all
you need is what the client owes on the case, take column U and stop reading.

### "COSTS did not add up against the itemization, so that balance is ICOS's category total rather than a per-fee breakdown..."

ICOS gives fees two ways: a five-line summary at the top, and an itemized list
underneath. Napier adds up the itemization and checks it against the summary.
When they don't match for a category, Napier trusts the summary and puts that
category's whole balance in one column instead of splitting it.

The sentence names the categories it happened to, and if a balance ended up in
MISCELLANEOUS, it says so. It ends one of two ways:

- "...**The rest of the row is fee by fee** and the ICOS total is still right."
  Only the named categories are lumped. Everything else on the row split
  normally.
- "...**Each balance is in the fee column its itemized lines name**, and the
  ICOS total is still right." Nothing on this row split. Every column on the
  row is a category total.

**What to do:** use column U for the amount owed. Don't quote an individual fee
column off this row as if it were a single fee. If a specific fee matters, for
example you're arguing about jail or room and board, get an accounting from the
clerk.

### "Fee columns are ICOS's five summary categories, not a per-fee breakdown. The itemization could not be reconciled against them, so sheriff, indigent defense, jail and probation fees are inside MISCELLANEOUS rather than in their own columns. The ICOS total is still right."

The same thing, but for a case where nothing at all reconciled. The row is
ICOS's five summary lines and nothing finer. Sheriff, indigent defense, jail and
probation money is sitting in MISCELLANEOUS.

**What to do:** same as above. Use column U. If you need to know how much of it
is jail fees, that answer isn't on this row, it's in ICOS.

### "ICOS gave no category summary for this case, so the fee columns come from the itemization. ICOS leaves the itemization's paid column blank on most fees, so anything already paid may still be counted here. Treat these as assessed rather than owed."

The opposite problem. There was no summary to work from, so the row is built
out of the itemized list. ICOS usually doesn't fill in what was paid on the
itemization, so the figures here are what was *charged* over the life of the
case, not what's still outstanding. The client may have paid some of it.

**What to do:** this is the one money note where the numbers may be higher than
what the client owes. Don't use it to tell a client what their balance is.
Check ICOS or the clerk for the current balance.

### "The part of COSTS that ICOS does not identify in the itemization is in UNKNOWN; the identified fees remain in their own columns."

Most of the category split fine. There was a leftover piece that ICOS doesn't
label, and that piece is in the UNKNOWN column.

**What to do:** nothing, unless the UNKNOWN figure is large enough to matter to
what you're doing. The rest of the row is good.

### "ICOS records payments per category, not per fee. The payment in COSTS could not be tied to a specific line, so that balance is ICOS's category total."

There was a payment on the case and ICOS records it against the category
without saying which fee it came off. Napier can't split a category once it
can't tell where the payment landed, so that one category stays whole.

**What to do:** nothing. The category total is right. The split inside it just
isn't available.

### "The COSTS balance is divided across its fee columns in proportion to what those fees were assessed, so the column totals are estimates. Request an accounting before relying on the split."

This one is different from the rest and worth reading carefully. Napier *did*
split the category across the fee columns, but it split it proportionally
because ICOS didn't say how the money actually applied. **The numbers in those
columns are estimates.**

**What to do:** the category total and column U are right. The individual
columns are Napier's best guess at the split. If you're going to rely on one of
them, ask the clerk for an accounting first.

### "Category fees total $X but ICOS shows $Y due (summary figures) - trust the ICOS total; the difference is usually payments or third-party collection fees ICOS no longer counts"

The fee columns and the ICOS total disagree. When this happens the row's ICOS
total in column U is highlighted so you can spot it.

**What to do:** exactly what it says. Use column U. The gap is almost always
payments the client has made, or collection agency fees ICOS has stopped
counting.

---

## Coding notes: column G, column D, column H

These are the ones to take seriously. Every one of them means an analysis sheet
is about to answer a question based on something Napier had to guess at, or
based on a blank.

### "Iowa Courts recorded a disposition Napier does not recognise (WORDING), so this case is coded OTH, and the sheets do not agree on what that means. The licence sheet reads OTH as no conviction and the expungement sheet answers n/a, but the bankruptcy and exemption sheets sort this case's debt as a conviction's. Check this case in ICOS before relying on any of them."

A clerk entered a disposition wording Napier has never seen. The row is coded
OTH, which is Napier's "I don't know". The problem is that the sheets disagree
about OTH, so this row will read as no conviction on one sheet and as a
conviction on another.

**What to do:** open the case in ICOS and read the disposition yourself. Then
tell Alex what the wording was. Napier learns these one at a time, and once it's
added, every future client with that wording is coded correctly.

### "No count on this case has been adjudicated in Iowa Courts. The only disposition it carries is the status of the case as a whole (STATUS), which Napier does not translate into a CRS code, so column G is empty. The BANKRUPTCY, EXEMPTIONS and SOL sheets all read an empty column G as "open charge", so this case appears on those three as a charge still pending against the client. A case Iowa Courts has closed or transferred is not that. Check this case in ICOS before relying on any of them."

Different from the one above. Here no individual charge was ever ruled on. All
ICOS has is a status for the case as a whole, and Napier deliberately refuses to
turn most of those statuses into a code rather than guess.

The consequence is the important part: **three sheets will show this as a
pending charge against your client.** If the case was actually closed or moved
to another county, that's wrong, and a pending charge is exactly the thing that
blocks an expungement.

**What to do:** check ICOS. If the case is genuinely resolved, this row's answer
on BANKRUPTCY, EXEMPTIONS and SOL should be ignored. Send the status wording to
Alex.

### "Iowa Courts show no adjudication on this case, so column D is blank and column G has no code. Column D blank is what tells the EXPUNGEMENT sheet this charge is still pending, but the SOL sheet reads it as a date and a blank reads as 1900: it reports this row's indigent defense and collection costs as barred by the 20 year limit, counts jail and room & board as 20 years old, and leaves the rest of the balance out of all three of its columns. Nothing here has aged out, because there is no judgment yet. Treat this row's SOL figures as unanswered."

This is a genuinely pending case, and Napier is right to leave it blank. The
EXPUNGEMENT sheet handles that correctly. The SOL sheet does not, because it
does arithmetic on the date and reads a blank as the year 1900.

**What to do:** on this row only, ignore the SOL sheet. It will tell you the
debt is time barred. It isn't. There's no judgment yet for the clock to run
from. Everything else on the row is fine.

### "The only disposition Iowa Courts show on this case is an adjudication, and this is not a juvenile case number, so column G reads JUV. Clerks enter "Adjudicated" on probation violation and contempt counts as well as on juvenile ones. If that is what this is, the case's real disposition is not on the page and JUV is wrong: BANKRUPTCY and EXEMPTIONS both read JUV as no conviction and will treat this debt as dischargeable. Check it."

The case number isn't a juvenile number, but the only thing on the page is
"Adjudicated", which is the juvenile word. Clerks also use it for probation
violations and contempt.

**What to do:** check it, and check it before advising on a bankruptcy. If this
is really a probation violation on an adult conviction, the sheet is currently
telling you the debt is dischargeable when it may not be.

### "This case has 3 disposition dates (DATES). Column D counts the conviction date: DATE. For SOL analysis please review ICOS information for the timeline of debt assessed."

The counts on this case were disposed on different days. The CRS has one row
per case and one date column, so column D holds the conviction date, which is
the one the expungement waiting periods run from.

The SOL sheet runs its twenty year test off that same single date, and on a
case with debt assessed at several points in time, one date can't describe all
of it.

**What to do:** the expungement answer is right. If you're doing SOL work on
this case, go look at the actual timeline in ICOS.

### "Column H is blank because Iowa Courts give the adjudicated charge as ORDINANCE, a city or county ordinance citation with no state chapter in it, so Napier cannot tell whether this is a chapter 321 offence. LICENSE-REGIS reads the blank as "Registration only". If the ordinance mirrors a chapter 321 offence the licence is at stake too, so check this one before advising on it."

The charge is a city or county ordinance, so there's no state chapter number for
Napier to check against chapter 321. Column H, the "Vehicular?" column, is left
blank, and LICENSE-REGIS treats blank the same as no.

**What to do:** if you're advising on the client's driving licence, look up what
the ordinance covers. Plenty of city ordinances mirror chapter 321, and if this
one does, the licence consequence is real and the sheet is currently saying
"Registration only".

### "This case is adjudicated under more than 12 statutes and the expungement sheet only screens the first 12. The rest (STATUTES) have to be checked by hand against 901C.2."

The expungement sheet has twelve slots for statutes and this case has more than
twelve. The extras are named in the note.

**What to do:** check the named statutes by hand against 901C.2. Any one of them
could be a disqualifier, and the sheet has not looked at them.

### "CIV is not in this template, so no sheet in this Lite workbook counts this case. Build it on the full CRS to score it."

**Lite workbooks only.** The Lite template doesn't carry formulas for every
disposition code, and this row's code is one it doesn't have. The row is filled
in, but no sheet in the Lite workbook is scoring it.

**What to do:** rebuild the client on the full CRS if you need this case scored.

---

## Client notes: how the record was matched

### "Filed under NAME."

The workbook was built from more than one spelling of your client's name, and
this case is filed under the spelling given here rather than the primary one.
Usually a maiden name, a middle name, or a clerk's typo.

**What to do:** nothing, as long as the name looks like your client. If it
doesn't, this case may belong to someone else.

### "DOB-Unknown: matched on the name alone."

Iowa Courts listed this case with no date of birth, and it was picked on the
name only. Every other row in the workbook was confirmed against a date of
birth. This one was not.

**What to do:** confirm the case is your client's before using it. Common names
are the risk here.

---

## Quick reference

| If the note is about | Column U (total) | The fee columns | The analysis sheets |
|---|---|---|---|
| Any of the money notes | Correct | Coarser or estimated | Fine |
| "Treat these as assessed rather than owed" | Correct | May include paid amounts | Fine |
| OTH / unrecognised disposition | Correct | Fine | Disagree with each other, check ICOS |
| Empty column G / case status only | Correct | Fine | Show a pending charge that may not exist |
| Pending case, column D blank | Correct | Fine | SOL is wrong, expungement is right |
| JUV on an adult case number | Correct | Fine | Bankruptcy and exemptions may be wrong |
| Several disposition dates | Correct | Fine | Expungement right, check SOL by hand |
| Ordinance charge, column H blank | Correct | Fine | LICENSE-REGIS says "Registration only" without checking |
| More than 12 statutes | Correct | Fine | Expungement screened only the first 12 |
| Lite: code not in template | Correct | Fine | Nothing scored this row |
| Filed under / DOB-Unknown | Correct | Fine | Fine, but confirm it's your client |

## When to send something back to Alex

Two of these notes are Napier telling you it hit something it hasn't been
taught yet:

- "a disposition Napier does not recognise (WORDING)"
- "the status of the case as a whole (STATUS), which Napier does not translate"

Both name the exact wording in the note. Sending that wording back means it gets
added, and nobody sees that note on that wording again.
