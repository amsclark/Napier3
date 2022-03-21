from bs4 import BeautifulSoup
import platform

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'



def parse_search(html):
    with open(tmp_dir + "search_results.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    too_many_results = len(soup.find_all(text="Your query returned more than 200 records.")) > 0
    if too_many_results:
        print "Too Many Results"
    cases = []
    for row in soup.find_all('tr'):
        cols = row.find_all('td')
        if len(cols) != 6:
            continue
        case = {
            'id': list(cols[0].stripped_strings)[0].replace(u'\xa0', u' '),
            'title': cols[2].string,
            'name': cols[3].string.strip(),
            'dob': cols[4].string.replace(u'\xa0', u''),
            'role': cols[5].string
        }
        if case['id'] == 'Case ID':
            continue
        if any([case['id'] == c['id'] for c in cases]):
            print "Supressing duplicate case id", case['id']
            continue
        cases.append(case)
    return (cases, too_many_results)

def parse_case_summary(html, case):
    with open(tmp_dir + case['id'] + "_summary.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    case['county'] = soup.find_all('tr')[2].find_all('td')[0].string

def parse_case_charges(html, case): # parses html pulled from ICOS to create data for Disp Date, Charge Descriptions, Statutory reference, and dispo code
    with open(tmp_dir + case['id'] + "_charges.html", "w") as text_file: #opens the "charges" tab from ICOS for the particular case
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    charges = [] # declare empty list "charges"
    cur_charge = None # initialize current charge [?]
    cur_section = None # initialize current section of that charge [?]
    rows = soup.find_all('tr') # creates an array of all info between <tr></tr>
    for row in rows: # for each set of row data in the array "rows"
        cols = row.find_all('font') # creates a subarray for everything between a font tag  
        texts = [ # this strips out the html garbage and leaves strings
            ''.join(col.find_all(text=True))
                .replace(u'\xa0', u' ')
                .replace('\r', '') # replaces carriage returns w/ empty string
                .replace('\n', '') # replaces new lines w/ empty string
                .replace('\t', '') # replaces tabs w/ empty string 
                .strip() # strips out all of those empty strings [?]
            for col in cols
        ]

        if len(texts) == 0: # if there is nothing in the row, sends you back to the beginning of the loop
            continue
        if texts[0].startswith("Count"):    # IDs rows that start with "count" - 1 of 3
            if cur_charge is not None:  # [??] we could ID the count number here - do we need to?
                charges.append(cur_charge)
            cur_charge = {}
            cur_section = "Charge"

        if texts[0] == "Adjudication": # IDs rows that start with "Adjudication" - 2 of 3
            cur_section = "Adjudication"
        if texts[0] == "Sentence": # IDs rows that start with "Sentence" - 3 of 3
            cur_section = "Sentence"
        
        if cur_section == "Adjudication": 
            if len(texts) >= 4 and texts[0].startswith("Charge:"):
                cur_charge['charge'] = texts[1]
                cur_charge['description'] = texts[3]
            if len(texts) >= 4 and texts[0].startswith("Adjudication:"):
                cur_charge['disposition'] = texts[1]
                cur_charge['dispositionDate'] = texts[3]

    if cur_charge is not None:
        charges.append(cur_charge)
    case['charges'] = charges

def parse_case_financials(html, case):
    with open(tmp_dir + case['id'] + "_financials.html", "w") as text_file:
        text_file.write(html)
    soup = BeautifulSoup(html, 'html.parser')
    financials = []
    rows = soup.find('form').find_all('tr')
    for row in rows:
        cols = row.find_all('td')
        if cols[1].string == 'Detail':
            continue
        financials.append({
            'detail': cols[1].string,
            'amount': cols[4].string,
            'paid': cols[5].string,
            'paidDate': cols[6].string
        })
    case['financials'] = financials
