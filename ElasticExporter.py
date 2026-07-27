#!/usr/bin/env python3

from datetime import datetime, timedelta, timezone
from time import monotonic, sleep, strftime
from elasticsearch import Elasticsearch
from datetime import datetime
import json, csv, os, sys
import hashlib, gzip, shutil
import base64
import traceback


def GetListGroups(es, index_name, settings):
  search_q = { "aggs": { settings['field_name'] : { "terms": { "field": settings['field_name'] + ".keyword",
        "order": { "_count": "desc" },
        "size": 500 } } },
        "size": 0, "script_fields": {}, "stored_fields": [ "*" ],
        "_source": { "excludes": [] },
        "query": settings['query_filter'] }

  if 'field_filter' in settings.keys():
    print (search_q['query']['bool']['filter'] )
    search_q['query']['bool']['filter'] = [ { "match_phrase": { settings['field_name'] + ".keyword"  : settings['field_filter'] } } ]
    print (search_q['query']['bool']['filter'] )

  #results = es.search(index=index_name, body=json.dumps(search_q), request_timeout = 60 )  
  results = es.search(index=index_name, query=json.dumps(search_q['query']), size=500, aggs=search_q['aggs'] )  

  if not results['timed_out']:
    if results['aggregations'][settings['field_name']]['sum_other_doc_count'] != 0:
      print ("Got other %s, list is not complete" % field_filter)
    ResultsList = {}
    for item in results['aggregations'][settings['field_name']]['buckets']:
      ResultsList[  item['key'] ] =  item['doc_count']
    return ResultsList 
  else:
    print ("search timed out")
    return []
  return []


def MakeFolders(settings):
  if not os.path.exists(settings['fullpath']):
    print ("making folder : %s" % settings['fullpath'] )
    os.makedirs(settings['fullpath'])


def CountLines(filename):
  with open(filename) as f:
    for i, l in enumerate(f):
      pass
  return i + 1


def CalcChecksum(filename):
  try:
    BLOCKSIZE = 65536
    hasher = hashlib.sha1()
    with open(filename, 'rb') as afile:
      buf = afile.read(BLOCKSIZE)
      while len(buf) > 0:
        hasher.update(buf)
        buf = afile.read(BLOCKSIZE)
      file_sha1 = hasher.hexdigest()
      filesize = os.path.getsize(filename)
      return file_sha1, filesize
  except:
    print ("Failed to calculate sha1 or filesize")
    sys.exit(1)


def FinishFolder(settings, TotalEventCount):
  files = os.listdir(settings['fullpath'] )
  AllChecksums = {}
  AllCount = 0
  FileAllChecksums = settings['fullpath'] + '/all.checksums'
  if not os.path.exists ( FileAllChecksums ):
    for FileName in files:
      if FileName.endswith(".checksums"):
        FullFileName = settings['fullpath'] + '/' + FileName
        with open (FullFileName, 'r') as f:
          contents = json.loads( f.read() )
          for item in contents.keys():
            AllCount += contents[item]['events']
            AllChecksums[item] = contents[item]
    print ("Total items : %s" % AllCount)
    if AllCount == TotalEventCount:
      print ("Exported every item in the index")
      print (AllChecksums)
      with open(FileAllChecksums, 'w') as f:
        f.write( json.dumps( AllChecksums ))
    elif not settings['NoGroup']:
      print ("Exported filtered search")
      print ("AllCount                : %s" % AllCount)
    else:
      print (settings)
      print ("AllCount != TotalEventCount")
      print ("AllCount                : %s" % AllCount)
      print ("TotalEventCount (index) : %s" % TotalEventCount)
  else:
    print ("found all.checksums, nothing to do")


def progress_line(done, total, elapsed=0, width=30):
  ratio = min(done / total, 1) if total else 1
  filled = int(width * ratio)
  eta = 0 if not done or ratio >= 1 else elapsed * (total - done) / done
  minutes, seconds = divmod(max(0, int(eta)), 60)
  hours, minutes = divmod(minutes, 60)
  eta_text = '%02d:%02d:%02d' % (hours, minutes, seconds) if hours else '%02d:%02d' % (minutes, seconds)
  return "Progress: [%s%s] %.1f%% | %s/%s | ETA %s" % (
    '#' * filled, '-' * (width - filled), ratio * 100, f'{done:,}', f'{total:,}', eta_text)


def write_progress(line, final=False, stream=None):
  stream = stream or sys.stdout
  stream.write('\r\x1b[2K' + line + ('\n' if final else ''))
  stream.flush()


def SearchGroup(es, index_name, settings, field_filter, TotalExported = 0, ExcludeField = False, AllItems = False ):
  print ("Exporting : %s for index %s" % ( field_filter, index_name ) )
 
  if AllItems:
    ForcePIT = True
  else: 
    ForcePIT = False

  if settings['TimeSeries'] == False: #no @timestamp
    search_q = {
      "size" : settings['page_size'],
      "query" : settings['query_filter'] }

  else:
    search_q = {
      "size" : settings['page_size'],
      "query" : settings['query_filter'],
      "sort": [{ settings['timestamp'] : { "order": "asc", "format": "strict_date_optional_time_nanos" } }  ] }

  #apply field filter
  if 'field_filter' in settings.keys():
    search_q['query']['bool']['filter'] = [ { "match_phrase": { settings['field_name'] + ".keyword"  : settings['field_filter'] } } ]

  if ExcludeField:
    #All items with no group 
    search_q["query"]["bool"]["must_not"] =  [ { "exists": { "field": settings['field_name'] + ".keyword" } } ] 
  elif not AllItems:
    #select 1 group
    search_q["query"]["bool"]["filter"].append( { "match_phrase": { settings['field_name'] + ".keyword" : field_filter } }  )

  if settings['TimeSeries'] == False:
    print ("Removing time")
    search_q['sort'] = {"_shard_doc": "desc"}
    ForcePIT = True
        
  if True: #always use PIT
    PITKeepAlive = '1m'
    results = dict( es.open_point_in_time(index=index_name, keep_alive=PITKeepAlive))
    #print ("open point in time : %s" % results)
    search_q['pit'] = { 'id' : results['id'] }

    SortOrder = { settings['timestamp'] : { "order": "asc", "format": "strict_date_optional_time_nanos" } }
    results = es.search( query=search_q['query'], pit=search_q['pit'], size=settings['page_size'], sort=SortOrder, request_timeout=settings['request_timeout'], rest_total_hits_as_int=True)
    expected = results['hits']['total']

    print ("Total items to export : %s" % ( f'{expected:,}' ) )
    

    if expected == 0:
      print ("No events returned from search")
      return { "failed" : True, "message" : "search returned 0 results" }        

    CurrentExported = 0
    search_after = False
    first_page = results
    started = monotonic()
    while True:
      if first_page is not None:
        results = first_page
        first_page = None
      else:
        results = es.search(query=search_q['query'], pit=search_q['pit'], size=settings['page_size'], sort=SortOrder, request_timeout=settings['request_timeout'], search_after=search_after )

      CurrentExported += len (results['hits']['hits'] )
      write_progress(progress_line(CurrentExported, expected, monotonic() - started))


      msg_WriteResults = WriteResults(settings, field_filter, expected, results, IgnoreCount = True, ExcludeField = ExcludeField )
      if msg_WriteResults['failed']:
        return { "failed" : True, "message" : "Failed  writing file - (over 10k) export" }        
 
      if msg_WriteResults['sort']:
        search_after = msg_WriteResults['sort']

      #break if got all results
      if CurrentExported >= expected:
        break
      if settings['page_delay']:
        sleep(settings['page_delay'])

    write_progress(progress_line(CurrentExported, expected, monotonic() - started), final=True)
    results = es.close_point_in_time(body= search_q['pit'] )
    if not results['succeeded']:
      print ("issue closing point in time search : %s" % results)
    if msg_WriteResults['failed']:
      print (msg_WriteResults)
    return msg_WriteResults


def filter_hits(hits, field, expected):
  path = field.removesuffix('.keyword').split('.')

  def value(hit):
    current = hit.get('_source', {})
    for part in path:
      if not isinstance(current, dict) or part not in current:
        return None
      current = current[part]
    return current

  return [hit for hit in hits if value(hit) == expected]


def convert_timestamps(item, timestamp='@timestamp', utc_offset=7):
  item = json.loads(json.dumps(item))

  def convert(value):
    if isinstance(value, dict):
      for key, child in value.items():
        if key == timestamp and isinstance(child, str):
          parsed = datetime.fromisoformat(child.replace('Z', '+00:00'))
          value[key] = parsed.astimezone(timezone(timedelta(hours=utc_offset))).isoformat(timespec='milliseconds')
        else:
          convert(child)
    elif isinstance(value, list):
      for child in value:
        convert(child)

  convert(item)
  return item


def WriteResults(settings, field_filter, expected, results, IgnoreCount = False, ExcludeField = False):
  if not results['timed_out']:
    if results['_shards']['failed'] == 0:
      total = results['hits']['total']
      total = total['value'] if isinstance(total, dict) else total
      if total == expected or IgnoreCount or ExcludeField:
        ExportFile = open ( settings['fullpath'] + '/' + field_filter + '.ndjson', 'a')
        hits = results['hits']['hits']
        if settings.get('output_filter_field'):
          hits = filter_hits(hits, settings['output_filter_field'], settings['output_filter_value'])
        for item in hits:
          item = convert_timestamps(item, settings.get('timestamp', '@timestamp'), settings.get('export_utc_offset', 7))
          ExportFile.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')))
          ExportFile.write('\n')
        ExportFile.close()

        Message = { "failed" : False, "message" : "completed" } 
        #Get sort value for last item
        if 'sort' in results['hits']['hits'][-1].keys():
          Message['sort'] = results['hits']['hits'][-1]['sort']

        return Message 
      else:
        print ("Got a different count of results")
        print (total)
        print (expected)
        return { "failed" : True, "message" : "Got a different number of results" }        
    else:
      print ("some shards failed")
      return { "failed" : True, "message" : "some shards failed" }
  else:
    print ("search timed out")
    return { "failed" : True, "message" : "search timed out" }
  return { "failed" : True, "message" : "unknown" }


def ProcessGroup(es, index_name, settings, group, ExcludeField = False, AllItems = False ):
  #search and write results to disk
  message = SearchGroup(es, index_name, settings, group, ExcludeField = ExcludeField, AllItems = AllItems )

  source = settings['fullpath'] + '/' + group + '.ndjson'

  if message['failed']:
    print (message)
    if os.path.exists(source):
      print ("Failed, removing file")
      os.remove(source)  
  else:
    #This can happen when there are no items when exclude field
    if message['message'] == "search returned 0 results":
      return

    #write checksum file 
    file_sha1, filesize = CalcChecksum(source)  
    file_lc = CountLines(source)
    checksums = { group + ".ndjson" : { "sha1" : file_sha1, "size" : filesize, "events" : file_lc }}
    print ("Exported file stats : %s" % checksums)
    with open (settings['fullpath'] + '/' + group + '.checksums', 'w') as f:
      f.write(json.dumps(checksums))
      f.close()


def ExportIndex(es, settings, TimeSeries, ExcludeField = False, AllItems = True, Debug = False):

  if 'NoGroup' in settings.keys():
    if settings['NoGroup'] != False:
      print ("No time field - based on NoGroup in settings")
      ExcludeField = False #All in one export
      AllItems = True

  if not es.indices.exists( index = settings['index_name'] ):
    print ("Index does not exist : %s" % settings['index_name'] )

  #Create a check to see if all documents in this index have been exported
  if settings['debug']:
    print ("ExportIndex : %s" % settings)
  MakeFolders(settings)

  #Get a list of groups + group not exists
  if not AllItems:
    GroupList = GetListGroups(es, settings['index_name'], settings)

    if settings['debug']:
      print (len(GroupList))
    for group in GroupList.keys():
      if settings['debug']:
        print ( "%s : %s" % (group, f'{GroupList[group]:,}' ) )

      #delete uncompressed .ndjson file (possible error from last run)
      file_ndjson = settings['fullpath'] + '/' + group + '.ndjson'
      file_sha = settings['fullpath'] + '/' + group + '.checksums'

      if not os.path.exists( file_sha ):
        if os.path.exists( file_ndjson ):
          os.remove( file_ndjson )
        ProcessGroup(es, settings['index_name'], settings, group )
        if settings['export-csv']:
          convertCSV(file_ndjson, remove_source=True)

  #again for results with no group
  group = settings['FileNameOther']
  file_ndjson = settings['fullpath'] + '/' + group + '.ndjson'
  file_sha = settings['fullpath'] + '/' + group + '.checksums'
  if not os.path.exists( file_sha ):
    if os.path.exists( file_ndjson ):
      print ("Removing file %s and re-exporting results" % file_ndjson) 
      os.remove( file_ndjson )
    ProcessGroup(es, settings['index_name'], settings, group, ExcludeField = ExcludeField, AllItems = AllItems )
    if settings['export-csv']:
      convertCSV(file_ndjson, remove_source=True)
  
def CountExported(settings):
  total = 0
  if not os.path.exists(settings['fullpath']):
    return total
  for filename in os.listdir(settings['fullpath']):
    if filename.endswith('.checksums') and filename != 'all.checksums':
      with open(os.path.join(settings['fullpath'], filename), 'r') as f:
        total += sum(item['events'] for item in json.load(f).values())
  return total


#export events for an index
def ProcessIndex(settings, AllItems = True):
    es = settings['es']

    settings['output_name'] = settings.get('output_name') or settings['index_name'].replace('*', 'all')
    index_folder = settings['index_name'].replace('*', 'all')
    settings['fullpath'] = settings['backup_folder'] + '/' + index_folder + '/' + settings['output_name']
    settings['all_checksum']   = settings['fullpath'] + '/all.checksums'

    # Tests to see if script should be called
    # 1. check if the index exists
    # 2. check for all.checksums in the folder
    if es.indices.exists( index = settings['index_name'] ):
      if not os.path.exists(settings['all_checksum']):
        countItems = es.count(index=settings['index_name'], query=settings['query_filter'])
        matched = countItems['count']
        print ("Query matched %s documents" % f'{matched:,}')
        if input("Continue export? [y/N]: ").strip().lower() not in ('y', 'yes'):
          print ("Export cancelled")
          return
        try:
          ExportIndex(es, settings, 'none', AllItems = AllItems)
          #writes 'all.checksums' file
          #If the script is run again, it won't re-export a 2nd time
          FinishFolder(settings, matched)
          exported = CountExported(settings)
          if exported == matched:
            print ("VERIFIED: exported %s of %s matched documents" % (f'{exported:,}', f'{matched:,}'))
          else:
            print ("MISMATCH: exported %s of %s matched documents" % (f'{exported:,}', f'{matched:,}'))
        except Exception:
          print ("Export failed in ProcessIndex")
          traceback.print_exc()
      else:
        print ("found an all.checksums file, skipping folder %s" % settings['fullpath'] )
    else:
      print ("Index does not exist : %s" % settings['index_name'] )

#convertCSV functions for reading ndjson and writing csv
#nested objects are converted to dotted notation
# { "ip" : { "address" : "" }} 
# to { "ip.address" : "" }
def convertCSV_FlattenDict(item, baseName):
  NewItem = {}
  for item2 in item.keys():
    if baseName != '': 
      ItemKey = baseName + "." + item2
    else:
      ItemKey = item2
    if isinstance(item[item2], dict):
      NewItem.update( convertCSV_FlattenDict(item[item2], ItemKey) )
    else:
      NewItem[ItemKey] = json.dumps(item[item2], ensure_ascii=False) if isinstance(item[item2], (list, dict)) else item[item2]
  return NewItem

def convertCSV_FlattenItem(item):
  NewItem = {}
  for item2 in item.keys():
    if isinstance(item[item2], dict):
      if item2 == '_source':
        NewItem.update ( convertCSV_FlattenDict(item[item2], '') )
      else:
        NewItem.update ( convertCSV_FlattenDict(item[item2], item2) )
    else:
      NewItem[item2] = json.dumps(item[item2], ensure_ascii=False) if isinstance(item[item2], (list, dict)) else item[item2]
  return NewItem

def convertCSV_WriteCSVFile(FileJSON, FileCSV, EventKeys):
  with open(FileCSV, 'w', newline='')  as output_file:
    dict_writer = csv.DictWriter(output_file, fieldnames=EventKeys)
    dict_writer.writeheader()

    #read the JSON file again and write csv file
    with open(FileJSON, 'r') as f:
      for line in f:
        lineJSON = convertCSV_FlattenItem ( json.loads(line) )
        dict_writer.writerow(lineJSON)

#reads JSON file and finds all field names
#used for the first line of the csv file
def convertCSV_ReadJSONFile(FileName):
  EventKeys = set()
  with open(FileName, 'r') as f:
    for line in f:
      EventKeys.update(convertCSV_FlattenItem(json.loads(line)))
  return sorted(EventKeys)

def convertCSV(FileJSON, remove_source = False):
  FileCSV = os.path.splitext(FileJSON)[0] + '.csv'
  print ("converting file %s to csv" % FileJSON)
  EventKeys = convertCSV_ReadJSONFile(FileJSON)
  convertCSV_WriteCSVFile(FileJSON, FileCSV, EventKeys)
  if remove_source:
    os.remove(FileJSON)
  return FileCSV

def ProcessMultipleIndexes(settings):
  res = settings['es'].indices.get(index=settings['index_name'])
  #print list of indexes to be exported
  print ("Selected %s indices to export" % len(res.keys()))
  for index_name in res.keys():
    countItems = settings['es'].count( index = index_name )
    print ("Found index %s which contains %s documents" % ( index_name, f'{countItems["count"]:,}' ))

  for index_name in res.keys():
    settings['index_name'] = index_name
    ProcessIndex(settings)

if __name__ == "__main__":
  print ("This is the ElasticExporter library - please use ElasticExporterCLI.py instead")


