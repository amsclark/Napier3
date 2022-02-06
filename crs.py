from decimal import Decimal

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
            charge_key = charge_key.replace("['","")
            charge_key = charge_key.replace("']","")
            print(str(iterator)+": " + str(charge_key))
            charge_dict[charge_key] = charge_pair.get(charge_key) 
        print("charge_dict: "+str(charge_dict))
        iterator += 1
        
    sorted_tuples = sorted(charge_dict.items(), reverse=True, key=lambda item: item[1])
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
        return "R" # UNKNOWN
    if "DELINQUENT REVOLVING FUND" in detail:
        return "R" # UNKNOWN
        
    if "FINE" in detail:
        return "I" # FINE
    if "DEFERRED JUDGMENT CIVIL PENALTY" in detail:
        return "I" # FINE
    if "INFRACTIONS-PENALTIES AND FORFEITURES-CITY" in detail:
        return "I" # FINE
    if "NONSCHEDULED CHAPTER 321" in detail:
        return "I" # FINE
    if "SCHEDULED VIOLATION/NON-SCHEDULED" in detail:
        return "I" # FINE
    
    if "FILING" in detail:
        return "J" # FILING
    if "COURT COSTS" in detail:
        return "J" # FILING
    if "TRAFFIC/SIMP MISD APPEAL FEES" in detail:
        return "J" # FILING
    if "OTHER SIMPLE MISDEMEANORS" in detail:
        return "J" # FILING

    if "INDIGENT DEFENSE" in detail:
        return "K" # INDIGENT DEFENSE

    if "SURCHARGE" in detail:
        return "L" # SURCHARGE

    if "ROOM/BOARD" in detail:
        return "M" # JAIL / ROOM & BOARD

    if "RESTITUTION" in detail:
        return "N" # RESTITUTION

    if "THIRD PARTY" in detail:
        return "O" # LINEBARGER COLLECTION FEE

    if "REVENUE" in detail:
        return "O" #DEPARTMENT OF REVENUE COLLECTION FEE

    if "SHERIFF" in detail:
        return "P" # SHERIFF

    return "Q" # MISC

def process_financials(case, worksheet, row):
    financials = {}
    col = None
    for f in case['financials']:
        if f['amount'] is None:
            f['amount'] = '0'
        if not f['detail'].strip():
            financials[col] -= Decimal(f['paid'])
            continue
        col = get_finance_column(f['detail'])
        if col not in financials:
            financials[col] = Decimal(0)
        financials[col] += Decimal(f['amount'])
        financials[col] -= Decimal(f['paid'])

    for f in financials:
        worksheet[f + row] = financials[f]


def process_case(case, worksheet, row):
    i = str(row)
    worksheet['A' + i] = case['id']
    worksheet['B' + i] = case['county']
    charge = get_dominant_charge(case['charges'])
    if charge is None:
          worksheet['C' + i] = "n/a"
          # come back later and do this with a map / dictionary
          if case['id'][7:9]=="DR":
              worksheet['D' + i] = "Domestic relations [civil]"
          elif case['id'][7:9]=="DA":
              worksheet['D' + i] = "Domestic abuse [civil]"
          elif case['id'][7:9]=="SC":
              worksheet['D' + i] = "Small claims"
          elif case['id'][7:9]=="PC":
              worksheet['D' + i] = "post conviction relief"
          else:
              worksheet['D' + i] = "other civil"
          worksheet['E' + i] = "n/a"
          worksheet['F' + i] = "CIV"
          process_financials(case, worksheet, i)
          return
    
    worksheet['C' + i] = charge['dispositionDate']
    worksheet['D' + i] = charge['description']
    worksheet['E' + i] = charge['charge']
    worksheet['F' + i] = charge['disposition']

    process_financials(case, worksheet, i)