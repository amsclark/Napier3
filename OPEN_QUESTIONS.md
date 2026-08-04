# Open questions

Things Napier does deliberately that need someone who knows Iowa practice to
confirm or overrule. None of these are bugs as far as the code is concerned.
They are places where the right answer is a legal judgement rather than a
parsing one, so the code takes the conservative reading and says so.

Numbers here come from replaying real ICOS cases through the shipping path. The
corpus grows as counties are added to it: 210 cases when the oldest sections
below were measured, 300 for most of them, 400 now. Each section says which it
was measured against, and a section still quoting an older count has not been
re-measured rather than been checked and found unchanged. No client data, names
or case numbers appear in this repository.

## The SOL sheet does not account for all the debt

On the 300 case workbook the SOL sheet totals $35,326.37 against $46,785.31
owed. The missing $11,458.94 sits on 22 time-barred rows.

Measured differently from the sections below, and the method is the point. The
workbook was built through the shipping path and then recalculated by
LibreOffice, so these are the numbers the formulas produce rather than numbers
Python computed from the same inputs. The same run says the workbook carries no
error value in any cell on any sheet, and that every one of the 300 cases
reaches all nine sheets. BANKRUPTCY and EXEMPTIONS each total $46,785.31 and
reconcile to CASE DATA exactly. SOL is the only sheet that does not.

The formulas are the reason. A row past twenty years claims columns J and K in
"STRANGE-BARRED" and column L in "20 year old Jail / Room & Board", and the
"NO ARGUMENT" column only fires on rows that are not barred. So on a barred
case everything from sheriff fees through restitution appears in no column at
all. What falls out:

| falls outside every SOL column | amount |
|---|---|
| sheriff fees | $854.15 |
| miscellaneous | $239.00 |
| unknown | $4,582.92 |
| surcharges | $1,612.40 |
| fines | $442.50 |
| victim restitution | $3,727.97 |

If the sheet is meant to list only what an attorney can argue away, this is
correct and the total is not supposed to reconcile. If it is meant to sort all
the debt into barred and not barred, then "NO ARGUMENT" should be the balance
after the two barred columns rather than zero on a barred row.

## An undisposed case counted as time barred

The SOL sheet asks whether the adjudication date plus 7300 days is in the past.
A case ICOS has not disposed has no adjudication date, so CASE DATA column D is
blank, and a blank date in Excel is the first of January 1900. Every undisposed
case therefore answers yes and is filed as twenty years barred, which is the
opposite of what it is.

What that costs on the 300 captured cases is nothing. Six rows have a blank
disposition date, they carry $89.50 between them, and none of that $89.50 sits
in the three columns the barred rows claim from, so the sheet reports $0.00
either way.

It is left alone because the honest fix is not a date test. An undisposed case's
debt is not barred and it is not arguable yet either, so putting it anywhere on
the SOL sheet means deciding which column an open case belongs in, and that is
the same question as the shortfall above.

## An open charge sorted as though it were a conviction

The bankruptcy and exemption sheets ask whether a case carries a conviction
before deciding which column its debt belongs in. As of the fix that added the
acquitted, withdrawn, not filed and transferred codes to that question, every
disposition Napier writes is answered correctly except two, and one of them is
a case with no disposition at all.

A case ICOS has not disposed leaves column G empty, and column B on both sheets
turns that into the words "open charge". "open charge" is not on the list of
codes that mean no conviction, so the sheet sorts the debt as a convicted
client's: out of the fully dischargeable column, and out of "all exemptions
apply" into "federal only".

On the 300 captured cases this is 8 cases carrying $286.93 between them, of
which 2 have any debt at all. Every dollar is still on the sheet and the case
totals are right; what changes is which column a staffer reads it in.

It is left alone because an open case has not been convicted and has not been
cleared either, and neither existing column says that. The same question is
open on the SOL sheet above, and the answer should probably be the same on
both. Worth asking whether these sheets want a column for a case that is still
running, or whether an open case should simply be left out of the sorting.

## A disposition that says only that the case is over

A production run on 3 August 2026 met two dispositions Napier could not read.
CHANGE OF VENUE is now mapped: the charge went to another county and was
decided there, so this record carries no outcome, which is what the transfer
code has always meant. Two of the 300 captured cases carry it and were reading
as unrecognised until then.

CLOSED is deliberately not mapped. A case closes after a conviction and after a
dismissal alike, and the word does not say which. Mapping it either way would
put a number in a bankruptcy column on the strength of a guess, so it stays
OTH, which is the code that means Napier does not know, and it keeps being
emailed out while the run is happening.

Worth asking Iowa Legal Aid whether CLOSED means something specific in ICOS
practice, because if it does, it is one line to map.

## A guilty finding that does not say how it was arrived at

Widening the corpus to 400 cases across 68 counties turned up GUILTY - OTHER,
which none of the first 300 had shown. One case carries it on both counts, two
simple misdemeanours from 2014, with $640.00 still owed.

It is mapped to GTR. That is a decision worth stating plainly, because the
usual rule here is that Napier does not guess a code. This is not much of a
guess: the word is GUILTY. What "- OTHER" leaves open is only how the finding
was arrived at, and GTR and GPL are the two codes that would say. Choosing
between them changes nothing anyone can read off the workbook, because all 299
formulas in both templates that name either one name both, and none names one
without the other. A test fails if a formula is ever added that tells them
apart, and then this needs a real answer rather than a convenient one.

Leaving it OTH was the alternative and it was worse. OTH means Napier could not
read the word, LICENSE-REGIS reads that as no conviction, and the sheet was
answering "Z - Neither license nor registration" about a client with a
conviction and $640.00 of court debt, which is the pair of facts that puts a
registration hold in play in the first place. The row said so in column V. The
sheet still gave the wrong answer to whoever was reading the sheet.

Worth asking Iowa Legal Aid what GUILTY - OTHER means in ICOS practice, because
if it means something specific, the mapping should say so rather than lean on
nothing computing a difference.

## Why the appeal sheet is named after one county

ICOS has one wording for jail and room and board,
`REIMBURSE-SHERIFF-ROOM/BOARD/MEDICAL`. It appears on 12 lines worth
$18,939.56 across 11 of the 400 captured cases. Napier sends every one of them
to CASE DATA column L, which is where the SOL sheet's column D reads from and
where the whole POLK R&B APPEAL sheet reads from.

The reconciliation part of this is closed. Column L used to go empty whenever
the surrounding category did not add up against its itemization, because room
and board sits inside COSTS and the whole COSTS balance went to MISC. Since the
fee breakdown change, an unreconciled balance is apportioned across the columns
its own fees point at, and column L now agrees with the balance ICOS reports on
all 11 cases to the cent. Seven of them have money in play, totalling
$11,510.23.

Anyone re-checking that by hand should know that ICOS lists instalment payments
as continuation rows carrying a payment and no detail, so summing the assessed
column against the first payment on each line overstates what is owed. Two of
these cases look short by $92.67 and $98.00 that way and are correct.

What is still open is the sheet. It is four columns wide, it has no county
filter in any cell, and it reads every row of CASE DATA, so a workbook built
for a client with no Polk cases in it still renders one line per case with
Polk's name at the top. Of the 80 Polk cases now captured, one carries room and
board and it is paid off. Every case in the corpus that does owe room and board
is in Delaware, Linn or Wapello, which are the counties the sheet's own title
says it is not about.

So the question for Iowa Legal Aid is what that sheet is for. If the Polk
county attorney's office is the only one that will hear a room and board
appeal, the sheet wants a county filter and currently has none. If the name is
historical and the sheet is really about room and board anywhere in Iowa, it
wants renaming and nothing else. Nobody should have to guess which, and the
answer decides whether a client outside Polk gets looked at.

The narrower version of the same problem: the four columns do not carry column
V's note, so a zero there reads identically whether the case has no jail debt
or Napier could not break the number out. That matters less now that the
breakdown holds, and it is still true.

## An ordinance conviction the expungement sheet cannot rule out

The public intoxication, PAULA and prostitution columns used to ask whether the
adjudicated statute was `123.46`, `123.47` or `725.1` exactly, and Iowa Courts
never writes a section that bare, so they answered NO on every case. They read
the statute properly now. The one real conviction among the 300 captured cases
is a guilty plea to possession of alcohol by a person under the legal age,
charged under a city ordinance and printed as `PO/123.47(2)`, and it says YES.

Reading it that way is a decision, and it is the same one Napier already makes
for the licence column: an ordinance citation carrying a state section number is
treated as that state offence. It could be wrong in a specific way. Iowa Code
123.47(9) expunges a conviction "for a violation of this section", and a
conviction under a city ordinance is not that, however exactly the ordinance
copies the statute. If Iowa practice is that an ordinance conviction cannot be
expunged under 123.47, this column should say NO on `PO/123.47(2)` and the
licence column is probably reading ordinances too generously as well.

The reverse case is left blank rather than answered. A city numbering its own
code 123.47 and prosecuting somebody for supplying alcohol under it is not a
coincidence worth entertaining, so a match is treated as evidence. The absence
of one is not, because an ordinance may number an offence anything at all, so
`MA/62.01(120)-0198` gets no answer instead of a NO. The sheet reads the blank
as NO, which is the same thing on the page with an honest reason behind it.

## An ordinance prefix the misdemeanour columns step over

The same prefix that the public intoxication column now reads properly is
invisible to the two columns beside it. "MISDEMEANOR CONVICTION?" asks whether
any of the twelve split statutes is on the CODE SECTIONS felony list, and
"ELIGIBLE ADJ" asks whether any of them is on the misdemeanour exclusion list or
starts with one of seven chapter numbers. Every one of those tests matches from
the left, so a statute written `PO/123.46` is not `123.46` to any of them and
the case walks past all eight tests.

Probed one at a time against the sheet's own logic: `PO/123.46`, `MA-123.46`,
`PO/123.47(2)`, `PO/719.1`, `PO/724.4`, `SP/321J.21` and `123.49;PO/726.6` all
come back "eligible" where the same sections written bare come back
"ineligible misd".

On the 300 captured cases it changes no answer. Forty-three of them carry an
ordinance prefixed statute somewhere, 139 cases reach the eligibility column at
all, and 16 of those are ruled ineligible, so the tests are doing work. Only
four ordinance cases reach the column, `BO/40.02`, `FD/934`, `AL/ 3.03` and
`SL/716.8(2)`, and none of them is on an exclusion list under either reading.

Stripping the prefix before the split would be a two line change and it is not
being made, because the two columns read the same twelve slots and want
opposite treatment. Iowa Code 364.3(2) caps a municipal ordinance at simple
misdemeanour, so a conviction under one is a misdemeanour whatever section it
copies, and "MISDEMEANOR CONVICTION?" answering YES on an ordinance case is
right. It is right by accident, because the felony list missed the prefix, and
stripping the prefix would make it wrong the first time a city legislates on
something in chapters 708, 714, 716, 724 or 726. Which of the two answers an
ordinance is entitled to is the question in the section above, and building a
twelve column mechanism on a guess at it to move zero cases is worse than
saying so here.

## Whether a registration hold needs a collection referral

The ACTION LIST says how many convicted cases carry a balance the county
treasurer can refuse to renew a registration over, citing Iowa Code 321.40(6)
and 602.8107(7). Both of those turn on the debt being delinquent, and Napier
counts every conviction carrying a balance without asking.

Measured across the 300 captured cases: 52 convicted cases carry a balance,
$42,757.66 in total. Nineteen of them, $19,999.86, show a collection referral in
ICOS by wording, mostly `DELINQUENT REVOLVING FUND`, with
`IOWA DEPT OF REVENUE COLLECTIONS`, `THIRD PARTY` and `COLLECTION BY CO ATTY`
making up the rest. The other 33, $22,757.80, show none.

That gap looked like over-reporting until the payment records were read against
it. Fifty-one of the 52 have taken no payment in the last twelve months. One
case, $1,157.50, is being paid. Under 602.8107(2) court debt is delinquent 30
days after assessment unless there is a plan, so 51 of 52 are delinquent on the
face of it whether or not a referral has been recorded, and the count Napier
prints is either right or one case high.

Whether the absence of a referral marker on those 33 cases means anything under
321.40(6) is the question, and it is a question about Iowa collection practice
rather than about the code. Napier's rule is left as it is.

## Fees Napier will not classify

Both of these go to column P, UNKNOWN, rather than being guessed at.

`DNU-DELINQUENT REVOLVING FUND OBLIGATION` appears on 43 lines and is most of
what UNKNOWN carries. `COLLECTION BY CO ATTY (THRESHOLD MET)` and
`COLLECTION BY CO ATTY` appear on 8 lines between them. The second reads like a
collection cost, which would put it in column K where the statute of
limitations sheet can see it, but calling it that is a decision about what the
county attorney fee is rather than about what the page says.

## What a structured fine is made of

A structured fine is the instalment arrangement, not the debt. A clerk
itemizing one writes each component fee with the arrangement's name attached,
so the itemization carries `INDIGENT DEFENSE-STRUC FINES-REIMB STATE`,
`COURT REPORTER SERVICES STRUC FINE`, `TIME PAYMENT FEES-STRUCTURED FINES` and
`DOCKET PROC - STRUCT FINE ABOVE SIMP` alongside the fine itself. Napier read
all four as fines, because it tested for the word FINE before it tested for
anything more specific.

The clerk settled this rather than us. On two Polk cases the itemization ran
$79.00 over the summary's FINE and $79.00 under its COSTS, and $79.00 is
exactly those four fees, twice, to the cent. That is the shortfall and matching
excess the partition check exists to catch, so it caught it, and both rows told
the staffer their balances could not be broken down fee by fee. Only
`FINES AND FORFEITED BAIL-STRUCTURED FINES` matched the summary's FINE on the
nose. They are court costs collected under a fine arrangement, and they are
filed that way now.

Worth confirming with Iowa Legal Aid all the same, because two cases from one
county is thin evidence for a rule, even when both are exact. If some other
county's clerk files them the other way, the partition check will say so by
failing rather than by being quietly wrong, which is the reason to leave the
check alone rather than loosen it.

The same change fixed a surcharge that was missing its category by two letters.
ICOS abbreviates the word when the wording runs long, and
`DOMESTIC/SEXUAL ABUSE, STALKING, HUMAN TRAFF VICTIM SURCH` runs long. Napier
now reads SURCH. Nothing in 400 captured cases contains those letters and is
not a surcharge, and a test pins the wordings that were checked, but the
abbreviation is a guess about ICOS's habits rather than something the page
states.

## Refundables counted as a payment

`REFUNDABLES DUE TO PREPAID EXPENSES` shows up on 14 lines worth $681.69, and
Napier counts every one of them as money the client paid toward court debt.
A prepaid expense being refunded is probably not that.

The original reason for leaving it alone was that ICOS's own totals treat it as
a payment, so changing it would put Napier's arithmetic at odds with the
court's. That has been measured since and is not true: taking these lines out
of the payment history moves no fee column on the 300 case corpus, which totals
$46,147.49 either way. What is left is weaker but still enough to hold. The
wording does not say which direction the money ran. It sits on civil cases
rather than the criminal ones the workbook is mostly for. It is $299.44 on the
cases that own it, against $9,274.25 for the bare `REFUNDABLE` that was
excluded. And three of its lines are part paid, where every held-money wording
that was excluded is assessed and paid at the same amount on every line.

## ICOS case statuses with no CRS code

Napier reads the case level status only where it overlaps the per count
adjudication wordings, which is at DISMISSED and nowhere else. These are left
uncoded, and the case goes onto the sheet with the wording in column V so it is
visibly uncoded rather than quietly miscoded:

GUILTY PLEA/DEFAULT, VIOLATIONS HANDLED BY CLERK, BY TRIAL TO COURT, OTHER
JUDGMENT, TRANSFERRED, SMALL CLAIM-DISPOSED BY CLERK, CHANGE OF VENUE, CLOSED,
DEFAULTED, DEFERRED JUDGEMENT, DISCHARGE, CONVERTED TO SIMPLE MISDEMEANR.

Whether VIOLATIONS HANDLED BY CLERK is a guilty plea decides what five sheets
compute, which is why the code will not guess.

The last four turned up only once the corpus passed 90 pages, so the list should
be read as still growing rather than as finished. DEFERRED JUDGEMENT is the one
worth answering first. It looks like a plain DEF, which is a code the licence and
expungement sheets both test for, and it is the only wording here that names a
CRS code outright.

On the 300 case corpus this costs almost nothing, because a case-level status is
only consulted where no count was adjudicated, and that is 8 cases. Two carry a
status Napier cannot code, CLOSED with $197.43 and TRANSFERRED with none. The
other six have no disposition of any kind and are genuinely pending. Both of the
uncoded rows leave column G empty, which BANKRUPTCY, EXEMPTIONS and SOL all read
as "open charge", and the row says so in column V.

## Smaller ones

A case whose counts were adjudicated on different days is reported under the
last date that carries the winning disposition code, so the code and the date
on the row agree with each other. On one of the 210 cases that is 9 days
before the case's actual last adjudication, because the later count was
disposed under a different code. Pairing the code with its own date and
reporting the case as finished on its last day cannot both be true, and which
one the sheets want is a question about the sheets.

Column I, "Under supervison?", is answered off the sentence table on the
charges page, and comes out YES on 4 of the 300 captured cases. It is blank on
the rest, and blank covers two different things: a term that has run out, and a
term nobody can put an end date on. Three cases are the second sort, where ICOS
shows probation with a date and no duration.

The expungement sheet reads that column in `=IF('CASE DATA'!I4="YES",
SUM('CASE DATA'!J4:P4),"n/a")`, so a blank and a NO reach it identically and
"Amount of debt subject to 910.7?" says n/a either way. On the three cases with
an undated term that n/a is Napier not knowing rather than the answer being
none, and nothing on the sheet distinguishes the two. Whether that column should
be able to say so is a question about the sheet.

Money the clerk is holding on somebody's behalf is kept out of the payment
history by matching three whole wordings, `APPEARANCE BOND REFUND`,
`BONDS - ESCROW` and `REFUNDABLE`, which are the only three of their kind seen
across 300 cases. A fourth wording for the same thing would go back to being
counted as a payment. The match is deliberately not on the word `BOND`, because
a bond assignment fee is court debt and a forfeited bond is money the county
keeps, and deliberately not on the word `REFUNDABLE`, for the reason in the
prepaid expenses section above.

The workbook recognises a `TNSF` disposition code that the parser never
produces. Either ICOS has a wording for it that has not been seen in 210
cases, or the code is dead.

An Iowa city ordinance conviction that mirrors a chapter 321 offence: does it
put the licence at risk the way the state charge would? The LICENSE-REGIS
sheet's answer depends on it.
