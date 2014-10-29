#!/usr/bin/python

import argparse
import sys
import logging
import urllib2
import mechanize
from bs4 import BeautifulSoup
import base64
import peekviewstate
import re

log = logging.getLogger(__name__)

# URL for the tournament info page, with placeholder for ID
TOURN_URL = "http://tennislink.usta.com/Tournaments/TournamentHome/Tournament.aspx?T={}"

EVENT_TARGET = '__EVENTTARGET'
EVENT_ARGUMENT = '__EVENTARGUMENT'

def visitAllNodes(nodes):
  for n in nodes:
    if isinstance(n, peekviewstate.Pair) or isinstance(n, peekviewstate.Triple) or isinstance(n, peekviewstate.ArrayList):
      for cn in visitAllNodes(n.data):
        yield cn
    else:
      yield n

# The tounament page is an ASP.NET page. The way that ASP.NET works is that it keeps a lot
# of state in a form field called "__VIEWSTATE"; when the server sends an HTML page down,
# it marshals all the session state into a binary-encoded object, then encodes it with base64
# and puts it in the hidden __VIEWSTATE field in a form that wraps the whole page ("aspnetForm").
# Then, most of the links on the page don't actually have URLs; instead, they have javascript URLs
# which call a function, __doPostBack with two parameters (target and argument); the javascript
# function __doPostBack fills in two hidden fields in the form with the target and argument,
# then POSTs the whole form back to the server, which returns a new page with an updated
# __VIEWSTATE which contains the updated server/session state.
# 
# I found that the information on tournament results is returned from the server in this base64
# __VIEWSTATE field; I couldn't find it returned in any other place. This apparently means that
# there's Javascript somewhere on the page that knows how to decode the base64-encoded binary
# blob and extract the HTML-encoded results, but I don't know how that works at all.
# 
# Instead, I parse the view state using some code I found on the net; I don't understand the
# structure of it at all, but I just look at all the entities in the object for one that is the
# right type ('\x05', "SystemString") and which contains the string "CommonTable", because
# that seems to correctly identify the right string. Good enough for a hack.
# 
# __VIEWSTATE can be parsed a little better with a tool at http://ignatu.co.uk/ViewStateDecoder.aspx,
# but it doesn't appear to have a lot of internal clues as to structure; it's just a tree of
# pairs, triples, and arrays with a variety of data types; it's not at all clear how to infer
# the structure of the internal data without access to the source code. Absent that, you either
# end up with a hack like mine or something stupid like taking the second entry in the top Pair, then
# the first entry in the Triple, then the third from the last entry in the ArrayList.
# 
# Anyway, to crawl the tournament info out, I do the following:
#  * Fetch the top tournament page.
#  * Fill in the EVENT_TARGET with a magic value for the "Results" tab, which I found through
#    inspection (looking at the page element for the Results tab link).
#  * Submit the form, this loads the "results" page state
#  * Now, in __VIEWSTATE, we will find an encoded object which includes summary results:
#    a list of divisions, each with the top few games, each with a link to load the full
#    results for that division.
#  * Find the right node with the HTML for the results in the view state object by iterating
#    over all the nodes until I find a SystemString with "CommonTable" in it.
#  * Use BeautifulSoup to parse the HTML encoded in that string.
#  * For every <a> tag in the html which has a Javascript __doPostBack URL, this is a link
#    to load the full results for a division. Save the target & argument parsed out of that
#    link.
#  * Now, looping all the discovered division links:
#    - fill in the parsed target & argument
#    - submit the page's aspnetForm form
#    - this will result in the HTML for the division's results in the __VIEWSTATE field of
#      the returned page
#    - Parse view state, same as above, to find that HTML string
#    - Parse it with BeautifulSoup to get a DOM structure
#    - Then, traverse the rows in the table to extract the division name and each row of
#      results (one per match)
#    - Print one output row per match
#    - Then go back to the all-results page before submitting the form again for the next
#      parsed division link

def getTournInfo(id):
  """Given a tournament ID (e.g. 151446), fetch that tournament's results"""
  br = mechanize.Browser()
  # Fetch the tournament page. Initially populated with generic information ("Important Info" tab)
  response = br.open(TOURN_URL.format(id))
  br.select_form(name='aspnetForm')
  # Add fields for event target & argument (normally created at runtime by Javascript)
  br.form.new_control("text", EVENT_TARGET, {})
  br.form.new_control("text", EVENT_ARGUMENT, {})
  # This magic value loads the results tab
  br.form[EVENT_TARGET] = 'ctl00$mainContent$lnkbutDates';
  br.form[EVENT_ARGUMENT] = '';
  # Load results
  response = br.submit()
  br.select_form(name='aspnetForm')
  # Grab viewstate and decode
  viewState = br.form['__VIEWSTATE']
  vsData = base64.b64decode(viewState)
  objs, out = peekviewstate.parse(vsData)
  subpages = []
  # Look at all leaf nodes in the view state object tree
  for n in visitAllNodes(objs):
    # Find the HTML block for summary results
    if n._header == '\x05' and 'CommonTable' in n.data:
      # Parse as HTML
      soup = BeautifulSoup(n.data)
      # Look at all the <a> links
      for link in soup.find_all('a'):
        href = link.get('href')
        # Some don't have href, they are headers (e.g. "Consolation"), skip them
        if href:
          # Match the __doPostBack URL into target & argument and save them in subpages
          m = re.match(r'javascript:__doPostBack\(\\\'([^\\]+)\\\', \\\'([^\\]+)', href)
          if m:
            subpages.append(m.groups())

  # OK, all division links have been parsed, fetch each of them
  for target, argument in subpages:
    br.select_form(name='aspnetForm')
    br.form.new_control("text", EVENT_TARGET, {})
    br.form.new_control("text", EVENT_ARGUMENT, {})
    br.form[EVENT_TARGET] = target
    br.form[EVENT_ARGUMENT] = argument
    response = br.submit()
    br.select_form(name='aspnetForm')
    # Grab view state and parse as above, looking for the HTML block
    viewState = br.form['__VIEWSTATE']
    vsData = base64.b64decode(viewState)
    objs, out = peekviewstate.parse(vsData)
    for n in visitAllNodes(objs):
      if n._header == '\x05' and 'CommonTable' in n.data:
        soup = BeautifulSoup(n.data)
        # Parse the division name (in the first row of the first table: table, then tr,
        # then within a set of child tags)
        division = str(soup.table.tr.td.a.span.string).strip().replace('\\', '')
        
        # Look at all the <tr> elements at the top of the HTML blob (siblings to the
        # table tag above (HTML is wrong, this is such crap).
        # Each is a result row with 3 td cells
        for row in soup('tr', recursive=False):
          cells = [str(cell.string).strip().replace('\\', '') for cell in row('td')]
          # print a four-field result: division in first column, then parsed cells
          if len(cells) == 3:
            print "\t".join([division] + cells)
            
    # go back in the browser (to the overview results page) before submitting the
    # next link to details. May not be necessary, I didn't try it without it.
    br.back()

class Usage(Exception):
  def __init__(self, msg):
    self.msg = msg

def main(argv=None):
  if argv is None:
    argv = sys.argv
  try:
    try:
      parser = argparse.ArgumentParser(description='Get Tournament Info')
      parser.add_argument('--debug', action='store_true')
      parser.add_argument('--id', required=True, type=int)
      args = parser.parse_args()
    except argparse.ArgumentError, msg:
      raise Usage(msg)
    
    logging.basicConfig(level=logging.WARNING)
    if args.debug:
      logging.getLogger().setLevel(logging.DEBUG)
    
    getTournInfo(args.id)
    
  except Usage, err:
    print >>sys.stderr, err.msg
    print >>sys.stderr, "for help use --help"
    return 2

if __name__ == "__main__":
    sys.exit(main())
