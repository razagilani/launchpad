import pycountry 
from PyZ3950 import zoom

from django.conf import settings
from django.db import connection, transaction

from ui.templatetags.launchpad_extras import cjk_info
from ui.templatetags.launchpad_extras import clean_isbn, clean_oclc, clean_issn


GW_LIBRARY_IDS = [7, 11, 18, 21]


def _make_dict(cursor, first=False):
    desc = cursor.description
    mapped = [
        dict(zip([col[0] for col in desc], row))
        for row in cursor.fetchall()
    ]
    if first:
        if len(mapped) > 0:
            return mapped[0]
        return {}
    return mapped


def get_bib_data(bibid):
    query = """
SELECT bib_text.bib_id, title, author, edition, isbn, issn, network_number, 
       publisher, pub_place, imprint, bib_format, language, library_name, 
       RTRIM(wrlcdb.GetMarcField(%s,0,0,'856','','u',1)) as LINK,
       wrlcdb.GetAllBibTag(%s, '880', 1) as CJK_INFO
FROM bib_text, bib_master, library
WHERE bib_text.bib_id=%s
AND bib_text.bib_id=bib_master.bib_id
AND bib_master.library_id=library.library_id
AND bib_master.suppress_in_opac='N'"""
    cursor = connection.cursor()
    cursor.execute(query, [bibid, bibid, bibid])
    bib = _make_dict(cursor, first=True)
    # split up the 880 (CJK) fields/values if available
    if bib.get('CJK_INFO', ''):
        bib['CJK_INFO'] = cjk_info(bib['CJK_INFO'])
    try:
        language = pycountry.languages.get(bibliographic=bib['LANGUAGE'])
        bib['LANGUAGE_DISPLAY'] = language.name
    except:
        bib['LANGUAGE_DISPLAY'] = ''
    return bib


def get_bibids_from_isbn(isbn):
    isbn = clean_isbn(isbn)
    code = 'ISB3' if len(isbn) == 13 else '020N' 
    query = """
SELECT bib_index.bib_id, bib_master.library_id, 
       library_name, normal_heading, display_heading 
FROM bib_index, bib_master, library 
WHERE bib_index.index_code = %s
AND bib_index.normal_heading like %s
AND bib_index.bib_id=bib_master.bib_id 
AND bib_master.library_id=library.library_id"""
    cursor = connection.cursor()
    cursor.execute(query, (code, isbn))
    results = _make_dict(cursor)
    return [row['BIB_ID'] for row in results]


def get_bibids_from_issn(issn):
    issn = clean_issn(issn)
    query = """
SELECT bib_index.bib_id, bib_master.library_id, library.library_name
FROM bib_index,bib_master,library
WHERE bib_index.index_code='022A'
AND bib_index.display_heading=%s
AND bib_index.bib_id=bib_master.bib_id
AND bib_master.library_id=library.library_id"""
    cursor = connection.cursor()
    cursor.execute(query, [issn])
    results = _make_dict(cursor)
    return [row['BIB_ID'] for row in results]


def get_bibids_from_oclc(oclc):
    oclc = clean_oclc(oclc)
    query = """
SELECT bib_index.bib_id, bib_index.index_code, bib_index.normal_heading, 
       bib_index.display_heading, bib_master.library_id, library.library_name
FROM bib_index, bib_master, library
WHERE bib_index.index_code='035A'
AND bib_index.normal_heading = %s
AND bib_master.bib_id=bib_index.bib_id
AND bib_master.library_id=library.library_id"""
    cursor = connection.cursor()
    cursor.execute(query, [oclc])
    results = _make_dict(cursor)
    return [row['BIB_ID'] for row in results]


def get_holdings_data(bib_data):
    bibids = set()
    if bib_data.get('ISBN', ''):
        bibids.update(get_bibids_from_isbn(isbn=bib_data['ISBN']))
    if bib_data.get('ISSN', ''):
        bibids.update(get_bibids_from_issn(issn=bib_data['ISSN']))
    if bib_data.get('NETWORK_NUMBER', ''):
        bibids.update(get_bibids_from_oclc(oclc=bib_data['NETWORK_NUMBER']))
    holdings_list = []
    cursor = connection.cursor()
    for bibid in bibids:
        query = """
SELECT bib_mfhd.bib_id, mfhd_master.mfhd_id, mfhd_master.location_id,
       mfhd_master.display_call_no, location.location_display_name,
       library.library_name
FROM bib_mfhd INNER JOIN mfhd_master ON bib_mfhd.mfhd_id = mfhd_master.mfhd_id,
     location, library
WHERE mfhd_master.location_id=location.location_id
AND bib_mfhd.bib_id=%s
AND mfhd_master.suppress_in_opac !='Y'
AND location.library_id=library.library_id
ORDER BY library.library_name"""
        cursor.execute(query, [bibid])
        holdings_list += _make_dict(cursor)
    for holding in holdings_list:
        holding.update({
            'ELECTRONIC_DATA': get_electronic_data(holding['MFHD_ID']), 
            'AVAILABILITY': get_availability(holding['MFHD_ID'])})
    return holdings_list


def get_electronic_data(mfhd_id):
    query = """
SELECT mfhd_master.mfhd_id,
       RTRIM(wrlcdb.GetMfHDsubfield(%s,'856','u')) as LINK856u,
       RTRIM(wrlcdb.GetMfHDsubfield(%s,'856','z')) as LINK856z,
       RTRIM(wrlcdb.GetMfHDsubfield(%s,'852','z')) as LINK852z,
       RTRIM(wrlcdb.GetMfHDsubfield(%s,'852','a')) as LINK852a,
       RTRIM(wrlcdb.GetMfHDsubfield(%s,'852','h')) as LINK852h,
       RTRIM(wrlcdb.GetAllTags(%s,'M','866',2)) as LINK866
FROM mfhd_master
WHERE mfhd_master.mfhd_id=%s"""
    cursor = connection.cursor()
    cursor.execute(query, [mfhd_id]*7)
    return _make_dict(cursor, first=True)
       


def get_availability(mfhd_id):
    query = """
SELECT DISTINCT display_call_no, item_status_desc, item_status.item_status,
       permLocation.location_display_name as PermLocation,
       tempLocation.location_display_name as TempLocation,
       mfhd_item.item_enum, mfhd_item.chron, item.item_id, item_status_date,
       bib_master.bib_id
FROM bib_master
JOIN library ON library.library_id = bib_master.library_id
JOIN bib_text ON bib_text.bib_id = bib_master.bib_id
JOIN bib_mfhd ON bib_master.bib_id = bib_mfhd.bib_id
JOIN mfhd_master ON mfhd_master.mfhd_id = bib_mfhd.mfhd_id
JOIN mfhd_item on mfhd_item.mfhd_id = mfhd_master.mfhd_id
JOIN item ON item.item_id = mfhd_item.item_id
JOIN item_status ON item_status.item_id = item.item_id
JOIN item_status_type on item_status.item_status = item_status_type.item_status_type
JOIN location permLocation ON permLocation.location_id = item.perm_location
LEFT OUTER JOIN location tempLocation ON tempLocation.location_id = item.temp_location
WHERE bib_mfhd.mfhd_id = %s
ORDER BY PermLocation, TempLocation"""
    cursor = connection.cursor()
    cursor.execute(query, [mfhd_id])
    return _make_dict(cursor, first=True)

def _get_z3950_connection(server):
    conn = zoom.Connection(server['SERVER_ADDRESS'], server['SERVER_PORT'])
    conn.databaseName = server['DATABASE_NAME']
    conn.preferredRecordSyntax = server['PREFERRED_RECORD_SYNTAX']
    return conn

def _get_gt_holdings(query):
    results = []
    values = status = location = callno = ''
    arow= {}
    conn = _get_z3950_connection(settings.Z3950_SERVERS['GT'])
    results = []
    res = conn.search(query)
    for r in res:
        values = str(r)
        lines = values.split('\n')
        for line in lines:
            ind = line.find('publicNote')
            if ind != -1:
                ind = line.find(':')
                status = line[ind+2:]
            ind = line.find('callNumber')
            if ind != -1:
                ind = line.find(':')
                callno = line[ind+2:]
            ind = line.find('localLocation')
            if ind != -1:
                ind = line.find(':')
                location = line[ind+2:]
        arow = {'status':status, 'location':location, 'callno':callno}
        results.append(arow)
    conn.close()
    return results


def get_z3950_holdings(id, school, id_type):
    holding_found = False
    if school == 'GM':
        results = []
        values = status = location = callno = ''
        arow= {}
        conn = _get_z3950_connection(settings.Z3950_SERVERS['GM'])
        query = zoom.Query('PQF', '@attr 1=12 %s' % id)
        res = conn.search(query)
        for r in res:
            values = str(r)
            lines = values.split('\n')
            for line in lines:
                ind = line.find('availableNow')
                if ind != -1:
                    ind = line.find(':')
                    status = line[ind+2:]
                ind = line.find('callNumber')
                if ind != -1:
                    ind = line.find(':')
		    ind1 = line.find('\\')
                    callno = line[ind+2:ind1]
                ind = line.find('localLocation')
                if ind!= -1:
                    ind = line.find(':')
		    ind1 = line.find('\\')
                    location = line[ind+2:ind1]
		    holding_found = True
		if holding_found == True:
		    arow = {'status':status, 'location':location, 'callno':callno}
            	    results.append(arow)
		holding_found = False
        conn.close()
        return results
    elif school=='GT':
        if id_type == 'isbn':
            query = zoom.Query('PQF', '@attr 1=7 %s' % id)
        elif id_type == 'issn':
            query = zoom.Query('PQF', '@attr 1=8 %s' % id)
        elif id_type == 'oclc':
            query = zoom.Query('PQF', '@attr 1=1007 %s' % id)
        return _get_gt_holdings(query)

def get_gmbib_from_gwbib(bibid):
    query = """
SELECT bib_index.normal_heading
FROM bib_index 
WHERE bib_index.bib_id = %s
AND bib_index.index_code ='035A'
AND bib_index.normal_heading=bib_index.display_heading"""
    cursor = connection.cursor()
    cursor.execute(query, [bibid])
    results = _make_dict(cursor)
    return [row['NORMAL_HEADING'] for row in results]
