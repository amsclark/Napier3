# Iowa Court Scraper
This is a tool that collects court case data from Iowa's state court case information system and enters it into a spreadsheet created by Iowa Legal Aid.

## Created by Alex Kornya and contractors

## Stack
The server is a [Flask app](https://flask.palletsprojects.com/en/1.1.x/) written in Python 2. The front end is written in html and javascript and uses jQuery and Bootstrap.

## Flow
* The user enters a name and clicks search
* The server runs the name search on Iowa's court case information system, scrapes the results, and presents them to the user as a list of unique names / dates of birth and the number of cases for each
* The user then selects from the groups of cases and clicks Create CRS
* The front-end requests case details from the server, one case at a time
* For each request, the server collects case details from Iowa's court case information system and returns them to the front end
* The front-end aggregates the case details and then sends them back to the server as a group
* The server takes the grouped case details, enters them into the excel CRS, and returns the completed spreadsheet to the client

## Production
The application runs on [Heroku](https://www.heroku.com/)

## Development
You'll need

* The Github app (or just git)
* An editor (e.g. Visual Studio Code)
* Python 2

### Run the app
This guide assumes you are using Windows

* Clone the source code
* Open a powershell window and cd to the source directory  
`> cd C:\Users\BenSchoenfeld\Documents\GitHub\iowa-court-scraper\`
* Create a virtual environment  
`> C:\Python27\Scripts\virtualenv.exe venv`
* Install the libraries  
`> .\venv\Scripts\pip.exe install -r .\requirements.txt`
* Run the app  
`> .\venv\Scripts\python.exe .\app.py`

You should get a message in your console that the app is running  
` * Running on http://127.0.0.1:5000/ (Press CTRL+C to quit)`

Open a browser, go to the url, and use the website to create a spreadsheet.

### Development
Once you have created at least one spreadsheet, all the raw html downloaded from the court case information website will be saved in the `tmp` folder. You can stop the main app and use the two test scripts to help with development.

`> .\venv\Scripts\python.exe .\test_search_parser.py`

This will parse the intial search result and print all the cases found. This list of cases is normally passed back to the client, where it is used to run the detail searches.

`> .\venv\Scripts\python.exe .\test.py`

This will parse the case details and print the object created for each case. These objects are used to generate rows in the spreadsheet. This script can also be used to generate the spreadsheet, but those lines need to be uncommented.
