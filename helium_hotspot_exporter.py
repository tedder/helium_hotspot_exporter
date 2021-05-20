#!/usr/bin/env python3

# external packages
import prometheus_client
import requests
import dateutil.parser

# internal packages
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

HOTSPOT_UP = prometheus_client.Gauge('helium_hotspot_up', 'Census of hotspots in existence', ['hotspot_address', 'hotspot_name'])
HOTSPOT_ONLINE = prometheus_client.Gauge('helium_hotspot_online', 'Hotspot is listed as online', ['hotspot_address', 'hotspot_name'])
HOTSPOT_YES_LISTEN_ADDRS = prometheus_client.Gauge('helium_hotspot_has_listen_address', 'Hotspot shows a listen address', ['hotspot_address', 'hotspot_name'])

HOTSPOT_EXIST_EPOCH = prometheus_client.Gauge('helium_hotspot_existence_epoch_seconds', 'Time that hotspot has been in existence', ['hotspot_address', 'hotspot_name'])
HOTSPOT_HEIGHT = prometheus_client.Gauge('helium_hotspot_heights', 'Blockchain height of various states', ['hotspot_address', 'hotspot_name', 'state_type'])
HOTSPOT_SCALE = prometheus_client.Gauge('helium_hotspot_reward_scale', 'Reward scale of hotspot', ['hotspot_address', 'hotspot_name'])
HOTSPOT_SLOW_UPDATE_EPOCH = prometheus_client.Gauge('helium_hotspot_slow_update_epoch_seconds', 'Time since slow updates were last run', ['hotspot_address', 'hotspot_name'])
SLOW_NEARBY_HOTSPOTS = prometheus_client.Gauge('helium_hotspot_nearby_count', 'Number of hotspots nearby. Updated infrequently.', ['hotspot_address', 'hotspot_name', 'distance_m'])



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
    SLOW_DATA[addr]['distance_ret'] = req_get_json(mkurl('hotspots/location/distance', '?lat=', float(d['lat']), '&lon=', float(d['lng']), '&distance=', NEARBY_DISTANCE_M))
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

def get_hotspot_address(hotspot_name):
  ret = req_get_json(mkurl('hotspots/name/', hotspot_name))
  log.info(ret)
  # cowardly refuse to return an entry if there's > 1 with the same name
  if len(ret['data']) > 1:
    log.error(f"cowardly refusing to look up address for hotspot name {hotspot_name} as it isn't unique. There are {len(ret['data'])} hotspots with that name.")
  elif len(ret['data']) == 0:
    log.error(f"could not find address for hotspot name {hotspot_name}. It doesn't exist.")
  elif len(ret['data']) == 1:
    return ret['data'][0]['address']

  return None

def get_hotspot_name(hotspot_address):
  ret = req_get_json(mkurl('hotspots/', hotspot_address))
  return ret['data']['name']

def collect_hotspots():
  '''Using our environment config, collect all the hotspots we should
     monitor. Some of these are simply a hotspot, some are the hotspot
     owner. In the latter case, we should rescan it occasionally (say,
     hourly).
  '''

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
      ha = ha.strip().lower()
      log.info(f"adding explicit address {ha}")
      hotspot_addresses.append(ha)
  if oas := os.environ.get('OWNER_ADDRESSES', ''):
    for oa in oas.split(','):
      log.info(f"looking up hotspots by owner address {oa}")
      oa = oa.strip().lower()
      if haddrs := get_hotspots_by_owner(oa):
        log.info(f"got addresses for owner address {oa}: hotspots: {haddrs}")
        hotspot_addresses.extend(haddrs)

  # now that we have a big list o' addresses, get their names. We might have
  # been passed a name, but we may as well standardize this.
  hotspots = {}
  for ha in hotspot_addresses:
    hn = get_hotspot_name(ha)
    if not hn:
      log.error(f"could not find hotspot name for address {hn}. we'll exclude it from stats.")
      continue
    hotspots[ha] = hn
  return hotspots

def stats_for_hotspot(addr, hname):
  ret = req_get_json(mkurl('hotspots/', addr))
  d = ret['data']

  # this hotspot exists.
  HOTSPOT_UP.labels(addr,hname).set(1)

  HOTSPOT_HEIGHT.labels(addr,hname,'system').set(d['block'])
  HOTSPOT_HEIGHT.labels(addr,hname,'hotspot_current').set(d['status']['height'])
  HOTSPOT_HEIGHT.labels(addr,hname,'hotspot_added').set(d['block_added'])
  #HOTSPOT_HEIGHT.labels(addr,hname,'score_update').set(d[''])
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
  if len(d['status']['listen_addrs']):
    haz_addr = 1
  HOTSPOT_YES_LISTEN_ADDRS.labels(addr,hname).set(haz_addr)

  # slow_whatever
  slow_stats_for_hotspot(addr, hname, d)

HOTSPOTS = []
@REQUEST_TIME.time()
def stats():
  global HOTSPOTS
  if not HOTSPOTS:
    log.info("collecting hotspots.")
    HOTSPOTS = collect_hotspots()
    log.debug(f"created hotspot dict: {HOTSPOTS}")

  for addr,hname in HOTSPOTS.items():
    stats_for_hotspot(addr, hname)


if __name__ == '__main__':
  prometheus_client.start_http_server(9826)
  log.info("started prometheus on port 9826")
  while True:
    try:
      stats()
    except ValueError as ex:
      log.error(f"stats loop failed, {type(ex)}: {ex}")

    # sleep 30 seconds
    time.sleep(UPDATE_PERIOD)

