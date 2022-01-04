#!/usr/bin/env python3

# external packages
import prometheus_client
import requests
import dateutil.parser

# internal packages
import json
import datetime
import time
import os
import logging

# remember, levels: debug, info, warning, error, critical. there is no trace.
logging.basicConfig(format="%(filename)s:%(funcName)s:%(lineno)d:%(levelname)s\t%(message)s", level=logging.WARNING)
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
#log.setLevel(logging.INFO)

API_BASE_URL = os.environ.get('API_BASE_URL', 'https://api.helium.io/v1/')
# time to sleep between scrapes
UPDATE_PERIOD = int(os.environ.get('UPDATE_PERIOD', 30))
NEARBY_DISTANCE_M = int(os.environ.get('NEARBY_DISTANCE_M', 20*1000))

req = requests.session()


# Create a metric to track time spent and requests made.
REQUEST_TIME = prometheus_client.Summary('helium_hotspot_exporter_runtime_seconds', 'Time spent processing hotspot exporter')

HELIUM_PRICES = prometheus_client.Gauge('helium_price', 'USD price of token', ['token','price_source'])
HELIUM_PRICE_UPDATED_BLOCK = prometheus_client.Gauge('helium_price_updated_block', 'block where price was updated (if applicable)', ['token','price_source'])
HELIUM_PRICE_UPDATED_EPOCH = prometheus_client.Gauge('helium_price_updated_epoch_seconds', 'Time since price was updated (if applicable)', ['token','price_source'])

HOTSPOT_UP = prometheus_client.Gauge('helium_hotspot_up', 'Census of hotspots in existence', ['hotspot_address', 'hotspot_name'])
HOTSPOT_ONLINE = prometheus_client.Gauge('helium_hotspot_online', 'Hotspot is listed as online', ['hotspot_address', 'hotspot_name'])
HOTSPOT_YES_LISTEN_ADDRS = prometheus_client.Gauge('helium_hotspot_has_listen_address', 'Hotspot shows a listen address', ['hotspot_address', 'hotspot_name'])

HOTSPOT_EXIST_EPOCH = prometheus_client.Gauge('helium_hotspot_existence_epoch_seconds', 'Time that hotspot has been in existence', ['hotspot_address', 'hotspot_name'])
HOTSPOT_HEIGHT = prometheus_client.Gauge('helium_hotspot_heights', 'Blockchain height of various states', ['hotspot_address', 'hotspot_name', 'state_type'])
HOTSPOT_SCALE = prometheus_client.Gauge('helium_hotspot_reward_scale', 'Reward scale of hotspot', ['hotspot_address', 'hotspot_name'])
HOTSPOT_ACTIVITY_COUNT = prometheus_client.Gauge('helium_hotspot_activity_count', 'Counts for various activities by hotspot', ['hotspot_address','hotspot_name','activity_type'])
HOTSPOT_SLOW_UPDATE_EPOCH = prometheus_client.Gauge('helium_hotspot_slow_update_epoch_seconds', 'Time since slow updates were last run', ['hotspot_address', 'hotspot_name'])
SLOW_NEARBY_HOTSPOTS = prometheus_client.Gauge('helium_hotspot_nearby_count', 'Number of hotspots nearby. Updated infrequently.', ['hotspot_address', 'hotspot_name', 'distance_m'])
ACCOUNT_BALANCE = prometheus_client.Gauge('helium_account_balance', 'Token balance for a given account', ['account_address', 'token_type'])
ACCOUNT_BLOCK = prometheus_client.Gauge('helium_account_block_height', 'Block height the account was last updated', ['account_address'])
ACCOUNT_ACTIVITY_COUNT = prometheus_client.Gauge('helium_account_activity_count', 'Counts for various activities by account', ['account_address','activity_type'])


SLOW_DATA = {}
def slow_stats_for_hotspot(addr, hname, d):
  '''probably only update these ~hourly.'''

  now = datetime.datetime.now(datetime.timezone.utc)
  if not SLOW_DATA.get(addr):
    SLOW_DATA[addr] = {'last_updated': None}
  lu = SLOW_DATA[addr]['last_updated'] or datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

  if (now-lu).total_seconds() > 3600:
    # update.  

    # note 'location' uses 'lon', but the hotspot returns 'lng'
    dret = req_get_json(mkurl('hotspots/location/distance', '?lat=', float(d['lat']), '&lon=', float(d['lng']), '&distance=', NEARBY_DISTANCE_M))
    if not dret: return # bail for bad data
    SLOW_DATA[addr]['distance_ret'] = dret
    SLOW_DATA[addr]['last_updated'] = now


  if dr := SLOW_DATA[addr]['distance_ret']:
    nearby_count = len(dr['data'])
    if nearby_count > 0:
      # subtract ourselves
      nearby_count -= 1
    SLOW_NEARBY_HOTSPOTS.labels(addr,hname,NEARBY_DISTANCE_M).set(nearby_count)

  # hopefully lu has been updated, if not we'll use epoch zero
  lu = SLOW_DATA[addr]['last_updated'] or datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
  HOTSPOT_SLOW_UPDATE_EPOCH.labels(addr,hname).set( (now-lu).total_seconds() )

def mkurl(*args):
  return API_BASE_URL + ''.join([str(x) for x in args])

def req_get_json(url):
  try:
    log.debug(f"fetching url: {url}")
    ret = req.get(url)
    log.debug(f"fetch returned: {ret}")
    if ret and ret.ok:
      return ret.json()
  except json.JSONDecodeError as ex:
    log.error(f"failed to get {url}, {ex}")
  return {}

def normalize_hotspot_name(hname):
  return hname.strip().lower().replace(' ', '-')

def get_hotspots_by_account(account_addr):
  ret = req_get_json(mkurl('accounts/', account_addr, '/hotspots'))
  log.info(f"ghbo ret: {ret}")
  if not ret: return # check for bad data
  # cowardly refuse to return an entry if there's > 1 with the same name
  if d:= ret.get('data'):
    return [x['address'] for x in d]

  return None

def get_hotspot_address(hotspot_name):
  ret = req_get_json(mkurl('hotspots/name/', hotspot_name))
  log.info(f"gha ret: {ret}")
  if not ret: return # check for bad data
  # cowardly refuse to return an entry if there's > 1 with the same name
  if len(ret['data']) > 1:
    log.error(f"cowardly refusing to look up address for hotspot name {hotspot_name} as it isn't unique. There are {len(ret['data'])} hotspots with that name.")
  elif len(ret['data']) == 0:
    log.error(f"could not find address for hotspot name {hotspot_name}. It doesn't exist.")
  elif len(ret['data']) == 1:
    return ret['data'][0]['address']

def get_hotspot(hotspot_address):
  ret = req_get_json(mkurl('hotspots/', hotspot_address))
  if not ret: return
  return ret['data']

def collect_hotspots_and_accounts():
  '''Using our environment config, collect all the hotspots we should
     monitor. Some of these are simply a hotspot, some are the hotspot
     account. In the latter case, we should rescan it occasionally (say,
     hourly).
  '''

  collectables = {'hotspots': {}, 'accounts': []}
  hotspot_addresses = []
  if hnames := os.environ.get('HOTSPOT_NAMES', ''):
    log.info(f"looking up hotspots by name(s): {hnames}")
    for hn in hnames.split(','):
      hn = normalize_hotspot_name(hn)
      log.debug(f"normalized name: {hn}")
      if ha := get_hotspot_address(hn):
        log.info(f"got address {ha} for name {hn}")
        hotspot_addresses.append(ha)
  if haddrs := os.environ.get('HOTSPOT_ADDRESSES', ''):
    for ha in haddrs.split(','):
      ha = ha.strip()
      log.info(f"adding explicit address {ha}")
      hotspot_addresses.append(ha)
  if oas := os.environ.get('ACCOUNT_ADDRESSES', ''):
    for oa in oas.split(','):
      log.info(f"looking up hotspots by account address {oa}")
      oa = oa.strip()
      if haddrs := get_hotspots_by_account(oa):
        log.info(f"got addresses for account address {oa}: hotspots: {haddrs}")
        hotspot_addresses.extend(haddrs)

  # now that we have a big list o' addresses, get their names. We might have
  # been passed a name, but we may as well standardize this.
  hotspots = {}
  accounts = {}
  for ha in hotspot_addresses:
    h = get_hotspot(ha)
    if not h: return
    hn = h['name']
    ho = h['owner']
    accounts[ho] = accounts.get(ho,0) + 1
    if not hn:
      log.error(f"could not find hotspot name for address {hn}. we'll exclude it from stats.")
      continue
    hotspots[ha] = hn
  collectables['hotspots'] = hotspots
  collectables['accounts'] = accounts
  return collectables

def stats_for_hotspot(addr, hname):
  # do main stats
  d = get_hotspot(addr)
  if not d: return

  # this hotspot exists.
  HOTSPOT_UP.labels(addr,hname).set(1)

  HOTSPOT_HEIGHT.labels(addr,hname,'system').set(d['block'])
  if not d['status']['height']:
      log.warning("Hotspot %s not reporting height, this is a new hotspot"%hname)
      d['status']['height']=-0
  HOTSPOT_HEIGHT.labels(addr,hname,'hotspot_current').set(d['status']['height'])
  HOTSPOT_HEIGHT.labels(addr,hname,'hotspot_added').set(d['block_added'])
  #HOTSPOT_HEIGHT.labels(addr,hname,'score_update').set(d[''])
  if d['last_poc_challenge']:
    HOTSPOT_HEIGHT.labels(addr,hname,'last_poc_challenge').set(d['last_poc_challenge'])
  HOTSPOT_HEIGHT.labels(addr,hname,'hotspot_last_changed').set(d['last_change_block'])

  now = datetime.datetime.now(datetime.timezone.utc)
  tsd = dateutil.parser.parse(d['timestamp_added'])
  ts_delta = (now-tsd).total_seconds()
  HOTSPOT_EXIST_EPOCH.labels(addr,hname).set(ts_delta)

  isup = 0
  if d['status']['online'] == 'online':
    isup = 1
  HOTSPOT_ONLINE.labels(addr,hname).set(isup)

  haz_addr = 0
  if d['status']['listen_addrs'] and len(d['status']['listen_addrs']):
    haz_addr = 1
  else:
    log.warning("status for hotspot %s is incomplete. Maybe this is a new hotspot"%hname)
  HOTSPOT_YES_LISTEN_ADDRS.labels(addr,hname).set(haz_addr)

  # other stats
  hotspot_activity_counts(addr,hname)
  # todo: hotspots/addr/witnesses

  # slow stats
  slow_stats_for_hotspot(addr, hname, d)

def account_activity_counts(addr):
  cret = req_get_json(mkurl('accounts/', addr, '/activity/count'))
  log.debug(cret)
  if not cret: return # check for bad data
  # only send some of these; not sure if they are all in use
  for k,v, in cret['data'].items():
    if k.startswith( ('rewards_', 'payment_', 'assert_', 'add_gateway') ):
      log.info(f"- {k} = {v}")
      ACCOUNT_ACTIVITY_COUNT.labels(addr,k).set(v)

def hotspot_activity_counts(addr,hname):
  cret = req_get_json(mkurl('hotspots/', addr, '/activity/count'))
  # only send some of these; not sure if they are all in use
  log.debug(cret)
  if not cret: return # check for bad data
  for k,v, in cret['data'].items():
    if k.startswith( ('state_channel_', 'rewards_', 'poc_', 'consensus_', 'assert_') ):
      HOTSPOT_ACTIVITY_COUNT.labels(addr,hname,k).set(v)

def account_stats(addr):
  aret = req_get_json(mkurl('accounts/', addr))
  log.debug(aret)
  if not aret: return # check for bad data
  hnt_bal = 0
  dc_bal = 0
  if bal := aret['data']['balance']:
    # the balance is an integer. Move the decimal. (yes, we risk losing precision)
    hnt_bal = bal/10**8
  if bal := aret['data']['dc_balance']:
    # is DC too big? mine is zero
    dc_bal = bal
  ACCOUNT_BALANCE.labels(addr,'DC').set(dc_bal)
  ACCOUNT_BALANCE.labels(addr,'HNT').set(hnt_bal)
  ACCOUNT_BLOCK.labels(addr).set(aret['data']['block'])

def stats_for_account(addr):
  account_stats(addr)
  account_activity_counts(addr)


PRICE_TIME = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
def get_prices():
  global PRICE_TIME

  now = datetime.datetime.now(datetime.timezone.utc)
  if (now-PRICE_TIME).total_seconds() < 600:
    # don't run too often
    return

  # official/oracle
  oret = req_get_json(mkurl('oracle/prices/current'))
  log.debug(oret)
  if not oret: return # check for bad data
  d = oret['data']
  if d['price']:
    HELIUM_PRICES.labels('HNT', 'oracle').set(d['price']/10**8)
  HELIUM_PRICE_UPDATED_BLOCK.labels('HNT', 'oracle').set(d['block'])

  now = datetime.datetime.now(datetime.timezone.utc)
  ts = dateutil.parser.parse(d['timestamp'])
  HELIUM_PRICE_UPDATED_EPOCH.labels('HNT', 'oracle').set( (now-ts).total_seconds() )

  # unofficial sources follow
  bret = req_get_json('https://api.binance.com/api/v3/ticker/price?symbol=HNTUSDT')
  log.debug(bret)
  if not bret: return # check for bad data
  HELIUM_PRICES.labels('HNT', 'binance').set(bret['price'])

COLLECT = []
@REQUEST_TIME.time()
def stats():
  global COLLECT
  if not COLLECT:
    log.info("collecting hotspots.")
    COLLECT = collect_hotspots_and_accounts()
    log.debug(f"created hotspot dict: {COLLECT['hotspots']}")
    log.debug(f"created account dict: {COLLECT['accounts']}")

  for addr,hname in COLLECT['hotspots'].items():
    stats_for_hotspot(addr, hname)

  for addr,_ in COLLECT['accounts'].items():
    stats_for_account(addr)

  get_prices()

if __name__ == '__main__':
  prometheus_client.start_http_server(9829)
  log.info("started prometheus on port 9829")
  while True:
    try:
      stats()
    except ValueError as ex:
      log.exception(ex)
      log.error(f"stats loop failed, {type(ex)}: {ex}")

    # sleep 30 seconds
    time.sleep(UPDATE_PERIOD)

