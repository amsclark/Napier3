from decimal import Decimal
from openpyxl.styles import Alignment # Added import
from openpyxl.styles import Font, PatternFill

MISMATCH_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
MISMATCH_FONT = Font(color="9C0006")

charge_code_map = {
    "GUILTY": {"GTR":1},
    "GUILTY BY COURT": {"GTR":1},
    "GUILTY - NEGOTIATED/VOLUN PLEA": {"GPL":1},
    "CONVERT TO SIMPLE MISDEM": {"GPL":1},
    "ACQUITTED": {"ACQ":0},
    "DISMISSED": {"DISM":0},
    "DISMISSED BY COURT": {"DISM":0},
    "DISMISSED BY OTHER": {"DISM":0},
    "DEFERRED": {"DEF":2},
    "NOT GUILTY": {"ACQ":0},
    "WAIVED TO ADULT COURT": {"JWV":0},
    "ADJUDICATED": {"JUV":1},
    "WITHDRAWN": {"WTHD":0},
    "NOT FILED": {"NOTF":0},
    "CIVIL": {"CIV":0}
}


def get_dominant_charge(charges):
    if len(charges) == 0:
        return None
    iterator = 0
    delisted = charges[0]
    raw_charge = delisted['disposition']
    print("raw_charge: "+str(raw_charge))
    charge_dict = {}
    while iterator <= len(raw_charge)-1:
        disposition = raw_charge[iterator].replace("DNU-", "")
        if not disposition:
            charge_dict["NOTF"] = 0
            #try a get function instead that defaults to 'OTH'
        elif disposition not in charge_code_map:
            charge_dict["OTH"] = 3
        else:
            charge_pair = charge_code_map.get(disposition)
            print("charge_code_map.get(disposition):"+str(charge_code_map.get(disposition)))
            charge_key = str(charge_pair.keys())
            charge_key = charge_key.replace("dict_keys(['","")
            charge_key = charge_key.replace("'])","")
            print(str(iterator)+": " + str(charge_key))
            charge_dict[charge_key] = charge_pair.get(charge_key) 
        print("charge_dict: "+str(charge_dict))
        iterator += 1
    #sorted_tuples = sorted(charge_dict.items(), reverse=True, key=lambda item: item[1])
    sorted_tuples = sorted(charge_dict.items(), reverse=True, key=lambda item: item[1] if item[1] is not None else float('inf'))
    print("sorted_tuples: " + str(sorted_tuples))
    sorted_charge = sorted_tuples[0]
    #sorted_charge = {k: v for k, v in sorted_tuples}
    print("sorted_charge: " + str(sorted_charge))
    #(dominant_charge, score) = sorted_charge.popitem()
    dominant_charge = sorted_charge[0]
    delisted['disposition'] = dominant_charge
    print("dominant_charge:" + str(dominant_charge))
    return delisted


def get_primary_charge(charges):
    if len(charges) == 0:
        return None

    charge = charges[0]
    #creates list for [?]
    charge['code'] = None
    date = None
    for c in charges:
        disposition = c['disposition'].replace("DNU-", "")
        charge = c
        #print c
        if not disposition:
            charge['code'] = "NOTF"
        elif disposition not in charge_code_map:
            charge['code'] = "OTH"
        else:
            charge['code'] = charge_code_map[disposition]
        
    return charge

def get_finance_column(detail):
    if "COLLECTION BY CO ATTY" in detail:
        return "P" # UNKNOWN
    if "DELINQUENT REVOLVING FUND" in detail:
        return "P" # UNKNOWN
        
    if "FINE" in detail:
        return "R" # FINE
    if "DEFERRED JUDGMENT CIVIL PENALTY" in detail:
        return "R" # FINE
    if "INFRACTIONS-PENALTIES AND FORFEITURES-CITY" in detail:
        return "R" # FINE
    if "NONSCHEDULED CHAPTER 321" in detail:
        return "R" # FINE
    if "SCHEDULED VIOLATION/NON-SCHEDULED" in detail:
        return "R" # FINE
    
    #if "FILING" in detail:
    #    return "J" # FILING
    #if "COURT COSTS" in detail:
    #    return "J" # FILING
    #if "TRAFFIC/SIMP MISD APPEAL FEES" in detail:
    #    return "J" # FILING
    #if "OTHER SIMPLE MISDEMEANORS" in detail:
    #    return "J" # FILING

    if "INDIGENT DEFENSE" in detail:
        return "J" # INDIGENT DEFENSE

    if "SURCHARGE" in detail:
        return "Q" # SURCHARGE

    if "ROOM/BOARD" in detail:
        return "L" # JAIL / ROOM & BOARD

    if "RESTITUTION" in detail:
        return "S" # RESTITUTION

    if "THIRD PARTY" in detail:
        return "K" # LINEBARGER COLLECTION FEE

    if "REVENUE" in detail:
        return "K" #DEPARTMENT OF REVENUE COLLECTION FEE

    if "SHERIFF" in detail:
        return "M" # SHERIFF

    if "PROBATION" in detail:
        return "N" # PROBATION REVOCATION FEE

    return "O" # MISC

def is_excluded_fee(detail):
    """Fees ICOS lists but does not count toward the balance.

    A third-party (Linebarger) collection fee appears as a line item, yet ICOS
    leaves it out of the case totals entirely -- summing the itemization at face
    value put money in the collection-costs column that the defendant is not
    shown as owing.
    """
    return "THIRD PARTY" in (detail or "").upper()


def summary_financials(case):
    """Per-column amounts owed, taken from the ICOS summary table.

    The summary reflects payments; the itemization does not. Where ICOS breaks
    the balance out by category, that is the number to report.
    """
    columns = {}
    for category in case.get('summary_categories') or []:
        if is_excluded_fee(category['label']):
            continue
        due = category['due']
        if due is None:
            continue
        column = get_finance_column(category['label'])
        columns[column] = columns.get(column, Decimal(0)) + due
    return columns


def itemized_financials(case):
    financials = {}
    col = None
    previous_col = None

    for f in case['financials']:
        detail = f['detail'] or ''
        if is_excluded_fee(detail):
            # Not part of what ICOS says is owed; counting it inflated the
            # collection-costs column.
            previous_col = None
            continue

        if not detail.strip():
            if previous_col is not None:
                col = previous_col
            else:
                continue  # Skip only if we have no previous category
        else:
            # For rows with non-blank details, get new column categorization
            col = get_finance_column(detail)
            previous_col = col

        if col not in financials:
            financials[col] = Decimal(0)

        amount = f['amount'] if f['amount'] is not None else '0'
        paid = f['paid'] if f['paid'] is not None else '0'
        financials[col] += Decimal(amount)
        financials[col] -= Decimal(paid)

    return financials


def process_financials(case, worksheet, row):
    # Prefer ICOS's own per-category balances; fall back to the itemization for
    # cases where ICOS doesn't break the summary out by category.
    financials = summary_financials(case)
    source = 'summary'
    if not financials:
        financials = itemized_financials(case)
        source = 'itemized'

    total_due = None
    if 'total_due' in case:
        total_due = Decimal(case['total_due'].replace('$', '').replace(',', ''))

    for column, value in financials.items():
        worksheet[column + str(row)] = value
    if total_due is not None:
        worksheet['U' + str(row)] = total_due

    # If the per-category figures still don't add up to the balance ICOS
    # reports, the ICOS figure (column U) is the one to trust -- flag the row so
    # staff don't take the categories at face value.
    if total_due is not None:
        categorized = sum(financials.values(), Decimal(0))
        if abs(categorized - total_due) > Decimal('0.01'):
            cell_u = worksheet['U' + str(row)]
            cell_u.fill = MISMATCH_FILL
            cell_u.font = MISMATCH_FONT
            worksheet['V' + str(row)] = (
                "Category fees total $%s but ICOS shows $%s due (%s figures) - trust "
                "the ICOS total; the difference is usually payments or third-party "
                "collection fees ICOS no longer counts"
                % (categorized, total_due, source)
            )
            worksheet['V' + str(row)].font = MISMATCH_FONT

def process_case(case, worksheet, row):
    i = str(row)
    worksheet['A' + i] = case['id']
    worksheet['B' + i] = case['county']
    charge = get_dominant_charge(case['charges'])
    
    cell_E = worksheet['E' + i] # Get cell E

    if charge is None:
          worksheet['C' + i] = case['summary_created_date']
          worksheet['D' + i] = case['summary_disposition_date']
          # come back later and do this with a map / dictionary
          description_text = ""
          if case['id'][7:9]=="DR":
              description_text = "Domestic relations [civil] - " + case['summary_dispo_status']
          elif case['id'][7:9]=="DA":
              description_text = "Domestic abuse [civil] - " + case['summary_dispo_status']
          elif case['id'][7:9]=="SC":
              description_text = "Small claims - " + case['summary_dispo_status']
          elif case['id'][7:9]=="PC":
              description_text = "post conviction relief - " + case['summary_dispo_status']
          else:
              description_text = "other civil - " + case['summary_dispo_status']
          cell_E.value = description_text
          worksheet['F' + i] = "n/a"
          worksheet['G' + i] = "CIV"
          process_financials(case, worksheet, i)
          # return # This was here, but we want to apply alignment, so moved it down
    else:
        worksheet['C' + i] = charge['offenseDate']
        worksheet['D' + i] = charge['dispositionDate']
        cell_E.value = charge['description']
        worksheet['F' + i] = charge['charge']
        worksheet['G' + i] = charge['disposition']

    cell_E.alignment = Alignment(wrap_text=True) # Apply text wrapping

    # If charge was None, we still need to process financials if it wasn't returned early
    if charge is None:
        # process_financials(case, worksheet, i) # Already called above if charge is None
        return # Now we can return

    process_financials(case, worksheet, i)
