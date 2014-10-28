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

def getTournInfo(id):
  br = mechanize.Browser()
  response = br.open(TOURN_URL.format(id))
  br.select_form(name='aspnetForm')
  br.form.new_control("text", EVENT_TARGET, {})
  br.form.new_control("text", EVENT_ARGUMENT, {})
  br.form[EVENT_TARGET] = 'ctl00$mainContent$lnkbutDates';
  br.form[EVENT_ARGUMENT] = '';
  response = br.submit()
  br.select_form(name='aspnetForm')
  viewState = br.form['__VIEWSTATE']
  vsData = base64.b64decode(viewState)
  objs, out = peekviewstate.parse(vsData)
  subpages = []
  for n in visitAllNodes(objs):
    if n._header == '\x05' and 'CommonTable' in n.data:
      soup = BeautifulSoup(n.data)
      for link in soup.find_all('a'):
        href = link.get('href')
        if href:
          m = re.match(r'javascript:__doPostBack\(\\\'([^\\]+)\\\', \\\'([^\\]+)', href)
          if m:
            subpages.append(m.groups())

  for target, argument in subpages:
    br.select_form(name='aspnetForm')
    br.form.new_control("text", EVENT_TARGET, {})
    br.form.new_control("text", EVENT_ARGUMENT, {})
    br.form[EVENT_TARGET] = target
    br.form[EVENT_ARGUMENT] = argument
    response = br.submit()
    br.select_form(name='aspnetForm')
    viewState = br.form['__VIEWSTATE']
    vsData = base64.b64decode(viewState)
    objs, out = peekviewstate.parse(vsData)
    for n in visitAllNodes(objs):
      if n._header == '\x05' and 'CommonTable' in n.data:
        soup = BeautifulSoup(n.data)
        division = str(soup.table.tr.td.a.span.string).strip().replace('\\', '')
        for row in soup('tr', recursive=False):
          cells = [str(cell.string).strip().replace('\\', '') for cell in row('td')]
          if len(cells) == 3:
            print "\t".join([division] + cells)
            
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
