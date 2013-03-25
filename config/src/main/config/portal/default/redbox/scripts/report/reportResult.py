from com.googlecode.fascinator.api.indexer import SearchRequest
from java.io import ByteArrayInputStream, ByteArrayOutputStream
from com.googlecode.fascinator.common.solr import SolrResult
from java.net import URLEncoder
from java.util import TreeMap
from org.apache.commons.lang import StringEscapeUtils
from org.json.simple import JSONArray
from au.com.bytecode.opencsv import CSVParser
from au.com.bytecode.opencsv import CSVWriter
from java.io import StringWriter
from java.lang import String
import sys

class ReportResultData:

    def __init__(self):
        pass
    def __activate__(self, context):
        self.__reportResult = None
        self.auth = context["page"].authentication
        self.request = context["request"]
        self.response = context["response"]
        self.log = context["log"]
        self.reportManager = context["Services"].getService("reportManager")
        self.indexer = context['Services'].getIndexer()
        self.metadata = context["metadata"]
        self.systemConfig = context["systemConfig"] 
        self.__rowsFound = 0
        self.processed_results_list = []
        
        self.errorMsg = "" 
        if (self.auth.is_logged_in()):
            if (self.auth.is_admin()==True):
                self.buildDashboard(context)
            else:
                self.errorMsg = "Requires Admin / Librarian / Reviewer access." 
        else:
            self.errorMsg = "Please login."
        self.__reportSearch()
        
        

    def __reportSearch(self):
        self.reportId = self.request.getParameter("id")
        self.format = self.request.getParameter("format")
        self.report = self.reportManager.getReports().get(self.reportId)
        self.reportQuery = self.report.getQueryAsString()
        self.log.debug("Report query: " +self.reportQuery)
        
        req = SearchRequest(self.reportQuery)
        req.setParam("fq", 'item_type:"object"')
        req.setParam("fq", 'workflow_id:"dataset"')
        if (self.format == "csv"): 
            out = ByteArrayOutputStream()
            self.fields = self.systemConfig.getArray("redbox-reports","csv-output-fields")
            
              
            recnumreq = SearchRequest(self.reportQuery)
            recnumreq.setParam("fq", 'item_type:"object"')
            recnumreq.setParam("fq", 'workflow_id:"dataset"')
            
            recnumreq.setParam("rows", "0")
            self.indexer.search(recnumreq, out)
            recnumres = SolrResult(ByteArrayInputStream(out.toByteArray()))
            self.__rowsFound = recnumres.getNumFound()
            req.setParam("rows", "%s" % self.__rowsFound)
            req.setParam("csv.mv.separator",";")
            
            if self.fields is not None:
                fieldString = ""
                for field in self.fields:
                  fieldString = fieldString+ field.get("field-name")+","
                fieldString = fieldString[:-1]
                req.setParam("fl",fieldString)
                
            
            out = ByteArrayOutputStream()
            fileName = self.urlEncode(self.report.getLabel())
            self.log.debug("Generating CSV report with file name: " + fileName)
            self.response.setHeader("Content-Disposition", "attachment; filename=%s.csv" % fileName)
            self.indexer.search(req, out, self.format)
            csvResponseString = String(out.toByteArray(),"utf-8")
            csvResponseLines = csvResponseString.split("\n")
            
            self.out = self.response.getOutputStream("text/csv")
            sw = StringWriter()
            parser = CSVParser()
            writer = CSVWriter(sw)
            count = 0
            
            prevLine = ""
            badRowFlag = False
            
            for line in csvResponseLines:
                if badRowFlag:
                    try:
                        self.log.debug("Reporting - trying to append the previous line with the previous faulty one. Line appears as: %s" % prevLine + line)
                        csvLine = parser.parseLine(prevLine + line)
                        badRowFlag = False
                        prevLine = ""
                        self.log.debug("Reporting - remedy appears to have worked. Line appears as: %s" % prevLine + line)
                    except:
                        #We tried to rescue the file but failed on the second run so give up
                        writer.writeNext(["Failed to transfer record to CSV - check logs"])
                        self.log.error("Reporting threw an exception (report was %s); Error: %s - %s; Result line: %s" % (self.report.getLabel(), sys.exc_info()[0], sys.exc_info()[1], prevLine + line))
                else:
                    try: 
                        csvLine = parser.parseLine(line)
                        badRowFlag = False
                        prevLine = ""
                    except: 
                        #This can happen if there's a newline in the index data 
                        #so we raise the badRowFlag and see if we can join this
                        #row to the next one to fix it
                        self.log.debug("Reporting threw an exception but I'll see if it's just a formatting issue (report was %s); Error: %s - %s; Result line: %s" % (self.report.getLabel(), sys.exc_info()[0], sys.exc_info()[1], line))
                        badRowFlag = True
                        prevLine = line
                        continue
                
                if count == 0 :
                    for idx, csvValue in enumerate(csvLine):
                        csvLine[idx] = self.findDisplayLabel(csvValue)
            
                writer.writeNext(csvLine)

            self.out.print(sw.toString())
            self.out.close()
            
        else:    
            try:
                #Get a total number of records
                recnumreq = SearchRequest(self.reportQuery)
                recnumreq.setParam("fq", 'item_type:"object"')
                recnumreq.setParam("fq", 'workflow_id:"dataset"')
                recnumreq.setParam("rows", "0")
                out = ByteArrayOutputStream()
                self.indexer.search(recnumreq, out)
                recnumres = SolrResult(ByteArrayInputStream(out.toByteArray()))
                self.__rowsFound = recnumres.getNumFound()
                
                #Now do the search
                req.setParam("rows", "10")
                out = ByteArrayOutputStream()
                self.indexer.search(req, out)
                self.__reportResult = SolrResult(ByteArrayInputStream(out.toByteArray()))
            except:
                self.errorMsg = "Query failed - please review your report criteria."
                self.log.debug("Reporting threw an exception (report was %s): %s - %s" % (self.report.getLabel(), sys.exc_info()[0], sys.exc_info()[1]))


    def __checkResults(self):
        #This is a fix, required because our SOLR index doesn't support
        #all of the required reporting criteria - specifically exact/contains
        
        self.processed_results_list = []
        
        if self.__reportResult is None:
            return
        
        #Get the report criteria
        criteria = self.report.getCriteria()
        
        #For each result item we need to check that it matches the criteria
        for item in self.getReportResult():
            #Use last check to assist in the left-to-right check of operators
            lastCheck = True
            dropResultFlag = False
            
            #For each criteria item
            for criteria_item in criteria.getCriteria():
                #If the last criteria item didn't check out and the AND op is used, the record doesn't make it
                if not lastCheck and criteria_item.getOperator() == RedboxReport.KEY_CRITERIA_LOGICAL_OP_AND:
                    dropResultFlag = True
                    lastCheck = False
                    break
                     
                #If the query criteria allows nulls and the field is null, true
                if criteria_item.getAllowNulls() == "field_include_null":
                    if item.get(criteria_item.getSolr_field()) is None:
                        dropResultFlag = False
                        lastCheck = True
                        break
                
                #If the query criteria uses 'equals', check that it's an exact match
                if criteria_item.getMatchingOperator() == "field_match":
                    if item.get(criteria_item.getSolr_field()) != criteria_item.getValue():
                        dropResultFlag = True
                        lastCheck = False
                        break
            
            if not dropResultFlag:
                #Copy over to the new listing
                self.processed_results_list.append(item)
                
        self.__rowsFound = len(self.processed_results_list)

    def getProcessedResultsList(self):
        self.__checkResults()
        return self.processed_results_list

    def findDisplayLabel(self, csvValue):
        if self.fields is not None:
            for field in self.fields:
                if field.get("field-name") == csvValue:
                    return field.get("label")    
        return csvValue
                
    def getErrorMsg(self):
        return self.errorMsg
            
    def buildDashboard(self, context):
        self.velocityContext = context
               
    def getReportResult(self):
        return self.__reportResult.getResults()
    
    def getReportName(self):
        return self.report.getReportName()
    
    def getReportLabel(self):
        return self.report.getLabel()
    
    def urlEncode(self, text):
        return URLEncoder.encode(text, "utf-8")
        
    def escapeHtml(self, value):
        if value:
            return StringEscapeUtils.escapeHtml(value) or ""
        return ""
        
    def getRowsFound(self):
        return self.__rowsFound 