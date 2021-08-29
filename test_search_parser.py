import case_parser
import platform
import pprint

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'

with open(tmp_dir + "search_results.html", "r") as text_file:
    html = text_file.read()
    cases, too_many_results = case_parser.parse_search(html)

    print "Cases:"
    pprint.pprint(cases)

    print "Too Many Results", too_many_results
