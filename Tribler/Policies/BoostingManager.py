# Written by Egbert Bouman

import ConfigParser
import glob
import HTMLParser
import json
import logging
import os
import random
import time
import urllib

from binascii import hexlify, unhexlify
from collections import defaultdict
from hashlib import sha1

import libtorrent as lt

from Tribler.Core.Libtorrent.LibtorrentMgr import LibtorrentMgr
from Tribler.community.allchannel.community import AllChannelCommunity
from Tribler.community.channel.community import ChannelCommunity
from Tribler.Core.simpledefs import NTFY_TORRENTS, NTFY_UPDATE, NTFY_INSERT, DLSTATUS_SEEDING, NTFY_SCRAPE
from Tribler.Core.TorrentDef import TorrentDef, TorrentDefNoMetainfo
from Tribler.Core.DownloadConfig import DownloadStartupConfig
from Tribler.Main.globals import DefaultDownloadStartupConfig
from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue
from Tribler.dispersy.util import call_on_reactor_thread
from Tribler.Utilities.scraper import scrape_udp, scrape_tcp
from Tribler.Core.CacheDB.Notifier import Notifier
from Tribler.TrackerChecking.TrackerSession import MAX_TRACKER_MULTI_SCRAPE

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s.%(msecs).03dZ-%(levelname)s-%(message)s",
    datefmt="%Y%m%dT%H%M%S")
formatter.converter = time.gmtime
handler = logging.FileHandler("boosting.log", mode="w")
handler.setFormatter(formatter)
logger.addHandler(handler)

number_types = (int, long, float)

CONFIG_FILE = "boosting.ini"

def lev(a, b):
    "Calculates the Levenshtein distance between a and b."
    n, m = len(a), len(b)
    if n > m:
        # Make sure n <= m, to use O(min(n,m)) space
        a, b = b, a
        n, m = m, n

    current = range(n + 1)
    for i in range(1, m + 1):
        previous, current = current, [i] + [0] * n
        for j in range(1, n + 1):
            add, delete = previous[j] + 1, current[j - 1] + 1
            change = previous[j - 1]
            if a[j - 1] != b[i - 1]:
                change = change + 1
            current[j] = min(add, delete, change)

    return current[n]

class BoostingPolicy(object):

    def __init__(self, session):
        self.session = session

    def apply(self, torrents, max_active, key, key_check):
        eligible_and_active = {}
        eligible_not_active = {}
        for k, v in torrents.iteritems():
            download = self.session.get_download(k)
            if download and download.get_share_mode():
                eligible_and_active[k] = v
            elif not download:
                eligible_not_active[k] = v

        # Determine which download to start.
        sorted_list = sorted([(key(v), k) for k, v in eligible_not_active.iteritems() if key_check(v)])
        infohash_start = sorted_list[0][1] if sorted_list else None

        # Determine which download to stop.
        if infohash_start:
            eligible_and_active[infohash_start] = torrents[infohash_start]
        sorted_list = sorted([(key(v), k) for k, v in eligible_and_active.iteritems() if key_check(v)])
        infohash_stop = sorted_list[-1][1] if sorted_list and len(eligible_and_active) > max_active else None

        return (infohash_start, infohash_stop) if infohash_start != infohash_stop else (None, None)


class RandomPolicy(BoostingPolicy):

    def apply(self, torrents_eligible, torrents_active):
        key = lambda v: random.random()
        key_check = lambda v: True
        return BoostingPolicy.apply(self, torrents_eligible, torrents_active, key, key_check)


class CreationDatePolicy(BoostingPolicy):

    def apply(self, torrents_eligible, torrents_active):
        key = lambda v: v['creation_date']
        key_check = lambda v: v['creation_date'] > 0
        return BoostingPolicy.apply(self, torrents_eligible, torrents_active, key, key_check)


class SeederRatioPolicy(BoostingPolicy):

    def apply(self, torrents_eligible, torrents_active):
        key = lambda v: v['num_seeders'] / float(v['num_seeders'] + v['num_leechers'])
        key_check = lambda v: isinstance(v['num_seeders'], number_types) and isinstance(v['num_leechers'], number_types) and v['num_seeders'] + v['num_leechers'] > 0
        return BoostingPolicy.apply(self, torrents_eligible, torrents_active, key, key_check)


class BoostingManager(object):

    __single = None

    def __init__(self, session, utility=None, policy=SeederRatioPolicy, src_interval=20, sw_interval=20, max_per_source=100, max_active=2):
        BoostingManager.__single = self

        self._saved_attributes = ["max_torrents_per_source",
                                  "max_torrents_active", "source_interval",
                                  "swarm_interval", "share_mode_target"]

        self.session = session
        self.utility = utility
        self.notifier = Notifier.getInstance()
        self.ltmgr = LibtorrentMgr.getInstance()
        self.tqueue = TimedTaskQueue("BoostingManager")
        self.credit_mining_path = os.path.join(DefaultDownloadStartupConfig.getInstance().get_dest_dir(), "credit_mining")
        if not os.path.exists(self.credit_mining_path):
            os.mkdir(self.credit_mining_path)

        self.boosting_sources = {}
        self.torrents = {}
        self.policy = None
        self.share_mode_target = 3

        self.max_torrents_per_source = max_per_source
        self.max_torrents_active = max_active
        self.source_interval = src_interval
        self.swarm_interval = sw_interval
        self.policy = policy(self.session)

        self.load_config()

        self.set_share_mode_params(share_mode_target=self.share_mode_target)

        if os.path.exists(CONFIG_FILE):
            logger.info("Config file %s", open(CONFIG_FILE).read())
        else:
            logger.info("Config file missing")

        self.tqueue.add_task(self._select_torrent, self.swarm_interval)
        self.tqueue.add_task(self.scrape_trackers, 30)

    def get_instance(*args, **kw):
        if BoostingManager.__single is None:
            BoostingManager(*args, **kw)
        return BoostingManager.__single
    get_instance = staticmethod(get_instance)

    def del_instance(*args, **kw):
        BoostingManager.__single = None
    del_instance = staticmethod(del_instance)

    def shutdown(self):
        for ihash, torrent in self.torrents.iteritems():
            self.stop_download(ihash, torrent)
        self.tqueue.shutdown(True)

    def load(self):
        if self.utility:
            try:
                string_to_source = lambda s: s.decode('hex') if len(s) == 40 and not (os.path.isdir(s) or s.startswith('http://')) else s
                for source in json.loads(self.utility.config.Read('boosting_sources')):
                    self.add_source(string_to_source(source))
                logger.info("Initial boosting sources %s",
                            self.boosting_sources.keys())
            except:
                logger.info("No initial boosting sources")

    def save(self):
        if self.utility:
            try:
                source_to_string = lambda s: s.encode('hex') if len(s) == 20 and not (os.path.isdir(s) or s.startswith('http://')) else s
                self.utility.write_config(
                    'boosting_sources',
                    json.dumps([source_to_string(source) for
                                source in self.boosting_sources.keys()]),
                    flush=True)
                logger.info("Saved sources %s", self.boosting_sources.keys())
            except:
                logger.exception("Could not save state")

    def set_share_mode_params(self, share_mode_target=None, share_mode_bandwidth=None, share_mode_download=None, share_mode_seeders=None):
        settings = self.ltmgr.ltsession.settings()
        if share_mode_target != None:
            settings.share_mode_target = share_mode_target
        if share_mode_bandwidth != None:
            settings.share_mode_bandwidth = share_mode_bandwidth
        if share_mode_download != None:
            settings.share_mode_download = share_mode_download
        if share_mode_seeders != None:
            settings.share_mode_seeders = share_mode_seeders
        self.ltmgr.ltsession.set_settings(settings)

    def add_source(self, source):
        if source not in self.boosting_sources:
            error = False
            args = (self.session, self.tqueue, source, self.source_interval, self.max_torrents_per_source, self.on_torrent_insert)
            if os.path.isdir(source):
                self.boosting_sources[source] = DirectorySource(*args)
            elif source.startswith('http://'):
                self.boosting_sources[source] = RSSFeedSource(*args)
            elif len(source) == 20:
                self.boosting_sources[source] = ChannelSource(*args)
            else:
                logger.error("Cannot add unknown source %s", source)
                error = True
            if not error:
                self.save_config()
        else:
            logger.info("Already have source %s", source)

    def remove_source(self, source_key):
        if source_key in self.boosting_sources:
            source = self.boosting_sources.pop(source_key)
            source.kill_tasks()
            self.save_config()

    def compare_torrents(self, t1, t2):
        ff = lambda ft: ft[1] > 1024 * 1024
        files1 = filter(ff, t1['metainfo'].get_files_with_length())
        files2 = filter(ff, t2['metainfo'].get_files_with_length())

        if len(files1) == len(files2):
            for ft1 in files1:
                for ft2 in files2:
                    if ft1[1] != ft2[1] or lev(ft1[0], ft2[0]) > 5:
                        return False
            return True
        return False

    def on_torrent_insert(self, source, infohash, torrent):
        # Remember where we got this torrent from
        if os.path.isdir(source) or source.startswith('http://'):
            source_str = source
        elif len(source) == 20:
            source_str = source.encode('hex')
        else:
            source_str = 'unknown source'
        torrent['source'] = source_str

        if self.boosting_sources[source].archive:
            torrent['preload'] = True
            torrent['prio'] = 100

        # Preload the TorrentDef.
        if torrent['metainfo']:
            if not isinstance(torrent['metainfo'], TorrentDef):
                torrent['metainfo'] = TorrentDef.load(torrent['metainfo'])
        else:
            torrent['metainfo'] = TorrentDefNoMetainfo(infohash, torrent['name'])

        # If duplicates exist, set is_duplicate to True, except for the one with the most seeders.
        duplicates = [other for other in self.torrents.values() if self.compare_torrents(torrent, other)]
        if duplicates:
            duplicates += [torrent]
            healthiest_torrent = max([(torrent['num_seeders'], torrent) for torrent in duplicates])[1]
            for torrent in duplicates:
                is_duplicate = healthiest_torrent != torrent
                torrent['is_duplicate'] = is_duplicate
                if is_duplicate and torrent.get('download', None):
                    self.stop_download(torrent['metainfo'].get_id(), torrent)

        self.torrents[infohash] = torrent
        logger.info("Got new torrent %s from %s", infohash.encode('hex'),
                    source_str)

    def scrape_trackers(self):
        num_requests = 0
        trackers = defaultdict(list)

        for infohash, torrent in self.torrents.iteritems():
            if isinstance(torrent['metainfo'], TorrentDef):
                tdef = torrent['metainfo']
                for tracker in tdef.get_trackers_as_single_tuple():
                    trackers[tracker].append(infohash)
                num_requests += 1

        results = defaultdict(lambda: [0, 0])
        for tracker, infohashes in trackers.iteritems():
            try:
                reply = {}
                if tracker.startswith("http://tracker.etree.org"):
                    for infohash in infohashes:
                        reply.update(scrape_tcp(tracker, (infohash,)))
                elif tracker.startswith("udp://"):
                    for group in range(len(infohashes) //
                                       MAX_TRACKER_MULTI_SCRAPE):
                        reply.update(scrape_udp(tracker, infohashes[
                            group*MAX_TRACKER_MULTI_SCRAPE:
                            (group+1)*MAX_TRACKER_MULTI_SCRAPE]))
                    reply.update(scrape_udp(tracker, infohashes[
                        -(len(infohashes)%MAX_TRACKER_MULTI_SCRAPE):]))
                else:
                    reply = scrape_tcp(tracker, infohashes)
                logger.debug("Got reply from tracker %s : %s", tracker, reply)
            except:
                logger.exception("Did not get reply from tracker %s", tracker)
            else:
                for infohash, info in reply.iteritems():
                    if info['complete'] > results[infohash][0]:
                        results[infohash][0] = info['complete']
                        results[infohash][1] = info['incomplete']

        for infohash, num_peers in results.iteritems():
            self.torrents[infohash]['num_seeders'] = num_peers[0]
            self.torrents[infohash]['num_leechers'] = num_peers[1]
            self.notifier.notify(NTFY_TORRENTS, NTFY_SCRAPE, infohash)

        logger.debug("Finished tracker scraping for %s torrents", num_requests)

        self.tqueue.add_task(self.scrape_trackers, 300)

    def set_archive(self, source, enable):
        if source in self.boosting_sources:
            self.boosting_sources[source].archive = enable
            logger.info("Set archive mode for %s to %s", source, enable)
            # TODO: update torrents
        else:
            logger.error("Could not set archive mode for unknown source %s",
                         source)

    def start_download(self, infohash, torrent):
        logger.info("Starting %s", hexlify(infohash))
        dscfg = DownloadStartupConfig()
        dscfg.set_dest_dir(self.credit_mining_path)

        preload = torrent.get('preload', False)
        torrent['download'] = self.session.lm.add(torrent['metainfo'], dscfg, pstate=torrent.get('pstate', None), hidden=True, share_mode=not preload)
        torrent['download'].set_priority(torrent.get('prio', 1))
        logger.info("Downloading torrent %s preload=%s",
                    infohash.encode('hex'), preload)

    def stop_download(self, infohash, torrent):
        logger.info("Stopping %s", hexlify(infohash))
        preload = torrent.pop('preload', False)
        download = torrent.pop('download')
        torrent['pstate'] = {'engineresumedata': download.handle.write_resume_data()}
        self.session.remove_download(download)
        logger.info("Removing torrent %s, preload=%s", infohash.encode('hex'),
                    preload)

    def _select_torrent(self):
        torrents = {}
        for infohash, torrent in self.torrents.iteritems():
            if torrent.get('preload', False):
                if not torrent.has_key('download'):
                    self.start_download(infohash, torrent)
                elif torrent['download'].get_status() == DLSTATUS_SEEDING:
                    self.stop_download(infohash, torrent)
            elif not torrent.get('is_duplicate', False):
                torrents[infohash] = torrent

        if self.policy and torrents:

            logger.debug("Selecting from %s torrents", len(torrents))

            ltmgr = LibtorrentMgr.getInstance()
            lt_torrents = ltmgr.ltsession.get_torrents()
            for lt_torrent in lt_torrents:
                status = lt_torrent.status()
                if unhexlify(str(status.info_hash)) in self.torrents:
                    logger.debug("Status for %s : %s %s", status.info_hash,
                                 status.all_time_download,
                                 status.all_time_upload)

            # Determine which torrent to start and which to stop.
            infohash_start, infohash_stop = self.policy.apply(torrents, self.max_torrents_active)

            # Start a torrent.
            if infohash_start:
                torrent = torrents[infohash_start]

                # Add the download to libtorrent.
                self.start_download(infohash_start, torrent)

            # Stop a torrent.
            if infohash_stop:
                torrent = torrents[infohash_stop]

                self.stop_download(infohash_stop, torrent)

        self.tqueue.add_task(self._select_torrent, self.swarm_interval)

    def load_config(self):
        config = ConfigParser.ConfigParser()
        config.read(CONFIG_FILE)
        for k, v in config.items(__name__):
            if k in self._saved_attributes:
                object.__setattr__(self, k, int(v))
            elif k == "policy":
                if v == "random":
                    self.policy = RandomPolicy(self.session)
                elif v == "creation":
                    self.policy = CreationDatePolicy(self.session)
                elif v == "seederratio":
                    self.policy = SeederRatioPolicy(self.session)
            elif k == "boosting_sources":
                for boosting_source in json.loads(v):
                    self.add_source(boosting_source)
            elif k == "archive_sources":
                for archive_source in json.loads(v):
                    self.set_archive(archive_source, True)

    def save_config(self):
        config = ConfigParser.ConfigParser()
        config.add_section(__name__)
        for k in self._saved_attributes:
            config.set(__name__, k, BoostingManager.__getattribute__(self, k))
        config.set(__name__, "boosting_sources",
                   json.dumps(self.boosting_sources.keys()))
        archive_sources = []
        for boosting_source_name, boosting_source in \
                self.boosting_sources.iteritems():
            if boosting_source.archive:
                archive_sources.append(boosting_source_name)
        if archive_sources:
            config.set(__name__, "archive_sources",
                       json.dumps(archive_sources))
        if isinstance(self.policy, RandomPolicy):
            policy = "random"
        elif isinstance(self.policy, CreationDatePolicy):
            policy = "creation"
        elif isinstance(self.policy, SeederRatioPolicy):
            policy = "seederratio"
        config.set(__name__, "policy", policy)
        with open(CONFIG_FILE, "w") as configf:
            config.write(configf)

class BoostingSource:

    def __init__(self, session, tqueue, source, interval, max_torrents, callback):
        self.session = session
        self.tqueue = tqueue

        self.torrents = {}
        self.source = source
        self.interval = interval
        self.max_torrents = max_torrents
        self.callback = callback
        self.archive = False

    def kill_tasks(self):
        self.tqueue.remove_task(self.source)

    def _load(self, source):
        pass

    def _update(self):
        pass


class ChannelSource(BoostingSource):

    def __init__(self, session, tqueue, dispersy_cid, interval, max_torrents, callback):
        BoostingSource.__init__(self, session, tqueue, dispersy_cid, interval, max_torrents, callback)

        self.channelcast_db = self.session.lm.channelcast_db

        self.community = None
        self.database_updated = True

        self.session.add_observer(self._on_database_updated, NTFY_TORRENTS, [NTFY_INSERT, NTFY_UPDATE])
        self.tqueue.add_task(lambda cid=dispersy_cid: self._load(cid), 0, id=self.source)

    def kill_tasks(self):
        BoostingSource.kill_tasks(self)
        self.session.remove_observer(self._on_database_updated)

    def _load(self, dispersy_cid):
        dispersy = self.session.get_dispersy_instance()

        @call_on_reactor_thread
        def join_community():
            try:
                self.community = dispersy.get_community(dispersy_cid, True)
                self.tqueue.add_task(get_channel_id, 0, id=self.source)

            except KeyError:

                allchannelcommunity = None
                for community in dispersy.get_communities():
                    if isinstance(community, AllChannelCommunity):
                        allchannelcommunity = community
                        break

                if allchannelcommunity:
                    self.community = ChannelCommunity.init_community(dispersy, dispersy.get_member(mid=dispersy_cid), allchannelcommunity._my_member, True)
                    logger.info("Joined channel community %s",
                                dispersy_cid.encode("HEX"))
                    self.tqueue.add_task(get_channel_id, 0, id=self.source)
                else:
                    logger.error("Could not find AllChannelCommunity")

        def get_channel_id():
            if self.community and self.community._channel_id:
                self.channel_id = self.community._channel_id
                self.tqueue.add_task(self._update, 0, id=self.source)
                logger.info("Got channel id %s", self.channel_id)
            else:
                logger.warning("Could not get channel id, retrying in 10 s")
                self.tqueue.add_task(get_channel_id, 10, id=self.source)

        join_community()

    def _update(self):
        if len(self.torrents) < self.max_torrents:

            if self.database_updated:
                infohashes_old = set(self.torrents.keys())

                torrent_keys_db = ['infohash', 'Torrent.name', 'torrent_file_name', 'creation_date', 'length', 'num_files', 'num_seeders', 'num_leechers']
                torrent_keys_dict = ['infohash', 'name', 'metainfo', 'creation_date', 'length', 'num_files', 'num_seeders', 'num_leechers']
                torrent_values = self.channelcast_db.getTorrentsFromChannelId(self.channel_id, True, torrent_keys_db, self.max_torrents)
                self.torrents = dict((torrent[0], dict(zip(torrent_keys_dict[1:], torrent[1:]))) for torrent in torrent_values)

                infohashes_new = set(self.torrents.keys())
                for infohash in infohashes_new - infohashes_old:
                    if self.callback:
                        self.callback(self.source, infohash, self.torrents[infohash])

                self.database_updated = False

            self.tqueue.add_task(self._update, self.interval, id=self.source)

    def _on_database_updated(self, subject, change_type, infohash):
        self.database_updated = True


class RSSFeedSource(BoostingSource):

    def __init__(self, session, tqueue, rss_feed, interval, max_torrents, callback):
        BoostingSource.__init__(self, session, tqueue, rss_feed, interval, max_torrents, callback)

        self.ltmgr = LibtorrentMgr.getInstance()
        self.unescape = HTMLParser.HTMLParser().unescape

        self.feed_handle = None

        self.tqueue.add_task(lambda feed=rss_feed: self._load(feed), 0, id=self.source)

    def _load(self, rss_feed):
        self.feed_handle = self.ltmgr.ltsession.add_feed({'url': rss_feed, 'auto_download': False, 'auto_map_handles': False})

        def wait_for_feed():
            # Wait until the RSS feed is longer updating.
            feed_status = self.feed_handle.get_feed_status()
            if feed_status['updating']:
                self.tqueue.add_task(wait_for_feed, 1, id=self.source)
            elif len(feed_status['error']) > 0:
                logger.error("Got error for RSS feed %s : %s",
                             feed_status['url'], feed_status['error'])
            else:
                # The feed is done updating. Now periodically start retrieving torrents.
                self.tqueue.add_task(self._update, 0, id=self.source)
                logger.info("Got RSS feed %s", feed_status['url'])

        wait_for_feed()

    def _update(self):
        if len(self.torrents) < self.max_torrents:

            feed_status = self.feed_handle.get_feed_status()

            torrent_keys = ['name', 'metainfo', 'creation_date', 'length', 'num_files', 'num_seeders', 'num_leechers']

            for item in feed_status['items']:
                # Not all RSS feeds provide us with the infohash, so we use a fake infohash based on the URL to identify the torrents.
                infohash = sha1(item['url']).digest()
                if infohash not in self.torrents:
                    # Store the torrents as rss-infohash_as_hex.torrent.
                    torrent_filename = os.path.join(BoostingManager.get_instance().credit_mining_path, 'rss-%s.torrent' % infohash.encode('hex'))
                    if not os.path.exists(torrent_filename):
                        try:
                            # Download the torrent and create a TorrentDef.
                            f = urllib.urlopen(self.unescape(item['url']))
                            metainfo = lt.bdecode(f.read())
                            tdef = TorrentDef.load_from_dict(metainfo)
                            tdef.save(torrent_filename)
                        except:
                            logger.error("Could not get torrent, skipping %s",
                                         item['url'])
                            continue
                    else:
                        tdef = TorrentDef.load(torrent_filename)
                    # Create a torrent dict.
                    torrent_values = [item['title'], tdef, tdef.get_creation_date(), tdef.get_length(), len(tdef.get_files()), -1, -1]
                    self.torrents[infohash] = dict(zip(torrent_keys, torrent_values))
                    # Notify the BoostingManager and provide the real infohash.
                    if self.callback:
                        self.callback(self.source, tdef.get_infohash(), self.torrents[infohash])

            self.tqueue.add_task(self._update, self.interval, id=self.source)


class DirectorySource(BoostingSource):

    def __init__(self, session, tqueue, directory, interval, max_torrents, callback):
        BoostingSource.__init__(self, session, tqueue, directory, interval, max_torrents, callback)

        self._load(directory)

    def _load(self, directory):
        if os.path.isdir(directory):
            self.tqueue.add_task(self._update, 0, id=self.source)
            logger.info("Got directory %s", directory)
        else:
            logger.error("Could not find directory %s", directory)

    def _update(self):
        if len(self.torrents) < self.max_torrents:

            torrent_keys = ['name', 'metainfo', 'creation_date', 'length', 'num_files', 'num_seeders', 'num_leechers']

            for torrent_filename in glob.glob(self.source + '/*.torrent'):
                if torrent_filename not in self.torrents:
                    try:
                        tdef = TorrentDef.load(torrent_filename)
                    except:
                        logger.error("Could not load torrent, skipping %s",
                                     torrent_filename)
                        continue
                    # Create a torrent dict.
                    torrent_values = [tdef.get_name_as_unicode(), tdef, tdef.get_creation_date(), tdef.get_length(), len(tdef.get_files()), -1, -1]
                    self.torrents[torrent_filename] = dict(zip(torrent_keys, torrent_values))
                    # Notify the BoostingManager.
                    if self.callback:
                        self.callback(self.source, tdef.get_infohash(), self.torrents[torrent_filename])

            self.tqueue.add_task(self._update, self.interval, id=self.source)
