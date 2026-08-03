# Open questions

Things Napier does deliberately that need someone who knows Iowa practice to
confirm or overrule. None of these are bugs as far as the code is concerned.
They are places where the right answer is a legal judgement rather than a
parsing one, so the code takes the conservative reading and says so.

Numbers here come from replaying 210 real ICOS cases through the shipping
path. No client data, names or case numbers appear in this repository.

## The SOL sheet does not account for all the debt

On the 210 case workbook the SOL sheet totals $8,720.48 against $16,602.57
owed. The missing $7,882.09 sits on 17 time-barred rows.

The formulas are the reason. A row past twenty years claims columns J and K in
"STRANGE-BARRED" and column L in "20 year old Jail / Room & Board", and the
"NO ARGUMENT" column only fires on rows that are not barred. So on a barred
case everything from sheriff fees through restitution appears in no column at
all. What falls out:

| falls outside every SOL column | amount |
|---|---|
| sheriff fees | $794.15 |
| miscellaneous | $340.93 |
| unknown | $1,915.76 |
| surcharges | $660.78 |
| fines | $442.50 |
| victim restitution | $3,727.97 |

If the sheet is meant to list only what an attorney can argue away, this is
correct and the total is not supposed to reconcile. If it is meant to sort all
the debt into barred and not barred, then "NO ARGUMENT" should be the balance
after the two barred columns rather than zero on a barred row.

Related: SOL column D is $0.00 across all 210 cases, because jail and room and
board debt reaches column L only on rows whose itemization reconciled fee by
fee. Offer to look at this is open and unanswered.

## Fees Napier will not classify

Both of these go to column P, UNKNOWN, rather than being guessed at.

`DNU-DELINQUENT REVOLVING FUND OBLIGATION` appears on 43 lines and is most of
what UNKNOWN carries. `COLLECTION BY CO ATTY (THRESHOLD MET)` and
`COLLECTION BY CO ATTY` appear on 8 lines between them. The second reads like a
collection cost, which would put it in column K where the statute of
limitations sheet can see it, but calling it that is a decision about what the
county attorney fee is rather than about what the page says.

## Refundables counted as a payment

`REFUNDABLES DUE TO PREPAID EXPENSES` shows up on 14 lines worth $681.69, and
Napier counts every one of them as money the client paid toward court debt.
A prepaid expense being refunded is probably not that. It is left alone because
the wording does not say clearly enough which way it runs, and because ICOS's
own totals treat it as a payment, so changing it would put Napier's arithmetic
at odds with the court's.

## ICOS case statuses with no CRS code

Napier reads the case level status only where it overlaps the per count
adjudication wordings, which is at DISMISSED and nowhere else. These are left
uncoded, and the case goes onto the sheet with the wording in column V so it is
visibly uncoded rather than quietly miscoded:

GUILTY PLEA/DEFAULT, VIOLATIONS HANDLED BY CLERK, BY TRIAL TO COURT, OTHER
JUDGMENT, TRANSFERRED, SMALL CLAIM-DISPOSED BY CLERK, CHANGE OF VENUE, CLOSED.

Whether VIOLATIONS HANDLED BY CLERK is a guilty plea decides what five sheets
compute, which is why the code will not guess.

## Smaller ones

A case whose counts were adjudicated on different days is reported under the
last date that carries the winning disposition code, so the code and the date
on the row agree with each other. On one of the 210 cases that is 9 days
before the case's actual last adjudication, because the later count was
disposed under a different code. Pairing the code with its own date and
reporting the case as finished on its last day cannot both be true, and which
one the sheets want is a question about the sheets.

Column I, "Under supervison?", is blank on all 210 cases. ICOS does not print
it anywhere Napier can reach.

Bond principal is kept out of the payment history by matching two whole
wordings, `APPEARANCE BOND REFUND` and `BONDS - ESCROW`, which are the only two
seen across 300 cases. A third wording for the same thing would go back to
being counted as a payment. The match is deliberately not on the word `BOND`,
because a bond assignment fee is court debt and a forfeited bond is money the
county keeps.

The workbook recognises a `TNSF` disposition code that the parser never
produces. Either ICOS has a wording for it that has not been seen in 210
cases, or the code is dead.

An Iowa city ordinance conviction that mirrors a chapter 321 offence: does it
put the licence at risk the way the state charge would? The LICENSE-REGIS
sheet's answer depends on it.
