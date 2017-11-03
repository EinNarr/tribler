"""
Supported boosting sources.

Author(s): Egbert Bouman, Mihai Capota, Elric Milon, Ardhi Putra
"""
import logging
import time
from binascii import hexlify, unhexlify

from twisted.internet import defer
from twisted.internet import reactor
from twisted.internet.task import LoopingCall

from Tribler.Core.CreditMining.credit_mining_util import source_to_string
from Tribler.Core.TorrentDef import TorrentDef
from Tribler.Core.simpledefs import NTFY_INSERT, NTFY_TORRENTS, NTFY_UPDATE
from Tribler.community.allchannel.community import AllChannelCommunity
from Tribler.community.channel.community import ChannelCommunity
from Tribler.dispersy.exception import CommunityNotFoundException
from Tribler.dispersy.taskmanager import TaskManager


class BoostingSource(TaskManager):
    """
    Base class for boosting source. For now, it is only channel source
    """

    def __init__(self, session, source, boost_settings, torrent_insert_cb):
        super(BoostingSource, self).__init__()
        self.session = session
        self.channelcast_db = session.lm.channelcast_db

        self.torrents = {}
        self.source = source
        self.interval = boost_settings.source_interval
        self.max_torrents = boost_settings.max_torrents_per_source
        self.torrent_insert_callback = torrent_insert_cb
        self.archive = False

        self.enabled = True

        self.av_uprate = 0
        self.av_dwnrate = 0
        self.storage_used = 0
        self.ready = False

        self.min_connection = boost_settings.min_connection_start
        self.min_channels = boost_settings.min_channels_start

        self._logger = logging.getLogger(BoostingSource.__name__)
        self._logger.setLevel(1)

        self.boosting_manager = self.session.lm.boosting_manager

        # no response for 2 hour, delete this torrent
        self.delete_idle_torrent_timeout = 7200
        self.recovery_blacklist_torrent = 18000

        # blacklisted torrent because of idleness. infohash:deleted_time
        self.blacklist_torrent = {}

    def start(self):
        """
        Start operating mining for this source
        """
        d = self._load_if_ready(self.source)
        self.register_task(str(self.source) + "_load", d, value=self.source)

        self.register_task("check_availability %s" %self.source, LoopingCall(self.check_available), 60, interval=60)
        self._logger.debug("Start mining on %s", source_to_string(self.source))

    def kill_tasks(self):
        """
        kill tasks on this source
        """
        self.ready = False
        self.cancel_all_pending_tasks()

    def _load_if_ready(self, source):
        """
        load source if and only if the overall system is ready.

        This is useful so we don't burden the application during the startup
        """
        def check_system(defer_param=None):
            """
            function that check the system whether it's ready or not

            it depends on #connection and #channel
            """
            if defer_param is None:
                defer_param = defer.Deferred()

            nr_channels = self.channelcast_db.getNrChannels()
            nr_connections = 0

            for community in self.session.lm.dispersy.get_communities():
                from Tribler.community.search.community import SearchCommunity
                if isinstance(community, SearchCommunity):
                    nr_connections = community.get_nr_connections()

            if nr_channels > self.min_channels and nr_connections > self.min_connection:
                defer_param.callback(source)
            else:
                self.register_task(str(self.source)+"_check_sys", reactor.callLater(10, check_system, defer_param))

            return defer_param

        defer_check = check_system()
        defer_check.addCallbacks(self._load, self._on_err)
        return defer_check

    def _load(self, source):
        pass

    def _update(self):
        pass

    def get_source_text(self):
        """
        returning 'raw' source. May be overriden
        """
        return self.source

    def _on_err(self, err_msg):
        self._logger.error(err_msg)


    def check_available(self):
        """
        Function to remove idle torrent from the list
        :return:
        """
        for infohash in list(self.torrents):

            # check if torrent already inserted or not
            if infohash not in self.session.lm.boosting_manager.torrents:
                continue
            elif 'download' not in self.torrents[infohash]:
                # or if it's stopped
                continue

            boosting_torrent = self.session.lm.boosting_manager.torrents[infohash]
            timediff = time.time() - boosting_torrent['time']['last_activity'] if boosting_torrent['time']['last_activity'] else 0

            # kill the torrent
            if timediff > self.delete_idle_torrent_timeout:
                # if currently running, stop it (this might not happen as the statistics will prevent this)

                if infohash in self.session.lm.boosting_manager.torrents \
                        and 'download' in self.session.lm.boosting_manager.torrents[infohash]:
                    self.session.lm.boosting_manager.stop_download(infohash, remove_torrent=True,
                                                                   reason="idle torrent deletion")
                else:
                    # also remove from boostingmanager
                    self.session.lm.boosting_manager.torrents.pop(infohash)

                self._logger.debug("Removed torrent %s from %s because of timeout", self.torrents[infohash]['name'] if infohash in self.torrents else hexlify(infohash), self.source)

                # remove from this source so it can make space
                self.torrents.pop(infohash)

                # blacklist this torrent for a while
                self.blacklist_torrent[infohash] = time.time()

        # recover blacklisted torrent
        for infohash in list(self.blacklist_torrent):
            if time.time() - self.blacklist_torrent[infohash] > self.recovery_blacklist_torrent:
                self._logger.debug("Recover blacklisted torrent %s", self.torrents[infohash]['name'] if infohash in self.torrents else hexlify(infohash))
                self.blacklist_torrent.pop(infohash)


class ChannelSource(BoostingSource):
    """
    Credit mining source from a channel.
    """
    def __init__(self, session, dispersy_cid, boost_settings, torrent_insert_cb):
        BoostingSource.__init__(self, session, dispersy_cid, boost_settings, torrent_insert_cb)

        self.channel_name = None
        self.channel_id = None

        self.channel_dict = None
        self.community = None
        self.database_updated = True

        self.check_torrent_interval = 10
        self.dispersy_cid = dispersy_cid

        self.torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)
        self.session.add_observer(self._on_database_updated, NTFY_TORRENTS, [NTFY_INSERT, NTFY_UPDATE])

        self.unavail_torrent = {}
        self.loaded_torrent = {}

    def kill_tasks(self):
        BoostingSource.kill_tasks(self)

        self.session.remove_observer(self._on_database_updated)

    def _load(self, dispersy_cid):
        dispersy = self.session.get_dispersy_instance()

        def join_community():
            """
            find the community/channel id, then join
            """#TODO can we find channel community?
            try:
                self.community = dispersy.get_community(dispersy_cid, True)
                self.register_task(str(self.source) + "_get_id", reactor.callLater(1, get_channel_id))

            except CommunityNotFoundException:
                allchannelcommunity = None
                for community in dispersy.get_communities():
                    if isinstance(community, AllChannelCommunity):
                        allchannelcommunity = community
                        break

                if allchannelcommunity:
                    self.community = ChannelCommunity.init_community(dispersy, dispersy.get_member(mid=dispersy_cid),
                                                                     allchannelcommunity._my_member, self.session)
                    self._logger.info("Joined channel community %s", dispersy_cid.encode("HEX"))
                    self.register_task(str(self.source) + "_get_id", reactor.callLater(1, get_channel_id))
                else:
                    self._logger.error("Could not find AllChannelCommunity")

        def get_channel_id():#TODO what is a channel ID
            """
            find channel id by looking at the network
            """
            if self.community and self.community._channel_id:
                self.channel_id = self.community._channel_id
                if self.community._channel_name:
                    self.channel_name = self.community._channel_name

                self.channel_dict = self.channelcast_db.getChannel(self.channel_id)
                task_call = self.register_task(str(self.source) + "_update",
                                               LoopingCall(self._update)).start(self.interval, now=True)
                if task_call:
                    self._logger.debug("Registering update call")

                self._logger.info("Got channel id %s", self.channel_id)

                self.ready = True
            else:
                self._logger.warning("Could not get channel id, retrying in 10 s")
                self.register_task(str(self.source) + "_get_id", reactor.callLater(10, get_channel_id))

        self.register_task(str(self.source) + "_join_comm", reactor.callLater(1, join_community))

    def _check_tor(self):
        """
        periodically check torrents in channel. Will return the torrent data if finished.
        """
        def showtorrent(torrent):
            """
            assembly torrent data, call the callback
            """
            infohash = torrent.infohash
            if torrent.get_files() and infohash in self.unavail_torrent:
                if len(self.torrents) >= self.max_torrents:
                    self._logger.debug("Max torrents in source reached. Not adding %s", self.torrents[infohash]['name'] if infohash in self.torrents else infohash)
                    del self.unavail_torrent[infohash]
                    return

                if infohash in self.blacklist_torrent:
                    self._logger.debug("Torrents blacklisted. Not adding %s", self.torrents[infohash]['name'] if infohash in self.torrents else hexlify(infohash))
                    del self.unavail_torrent[infohash]
                    return

                self._logger.debug("[ChannelSource] Got torrent %s", self.torrents[infohash]['name'] if infohash in self.torrents else hexlify(infohash))
                self.torrents[infohash] = {}
                self.torrents[infohash]['name'] = torrent.get_name()
                self.torrents[infohash]['metainfo'] = torrent
                self.torrents[infohash]['creation_date'] = torrent.get_creation_date()
                self.torrents[infohash]['length'] = torrent.get_length()
                self.torrents[infohash]['num_files'] = len(torrent.get_files())
                #TODO(ardhi) get seeder/leecher from db
                self.torrents[infohash]['num_seeders'] = 0
                self.torrents[infohash]['num_leechers'] = 0
                self.torrents[infohash]['enabled'] = self.enabled

                # seeding stats from DownloadState
                self.torrents[infohash]['last_seeding_stats'] = {}

                del self.unavail_torrent[infohash]

                if self.torrent_insert_callback:
                    self.torrent_insert_callback(self.source, infohash, self.torrents[infohash])
                self.database_updated = False

        if self.unavail_torrent and self.enabled:
            self._logger.debug("Unavailable #torrents : %d from %s", len(self.unavail_torrent), self.channel_name)
            for torrent in self.unavail_torrent.values():
                self._load_torrent(torrent[2]).addCallback(showtorrent)

    def _update(self):
        if len(self.torrents) < self.max_torrents and self.database_updated:
            CHANTOR_DB = ['ChannelTorrents.channel_id', 'Torrent.torrent_id', 'infohash', '""', 'length',
                          'category', 'status', 'num_seeders', 'num_leechers', 'ChannelTorrents.id',
                          'ChannelTorrents.dispersy_id', 'ChannelTorrents.name', 'Torrent.name',
                          'ChannelTorrents.description', 'ChannelTorrents.time_stamp', 'ChannelTorrents.inserted']

            torrent_values = self.channelcast_db.getTorrentsFromChannelId(self.channel_id, True, CHANTOR_DB,
                                                                          self.max_torrents)

            # dict {key_infohash(binary):Torrent(tuples)}
            self.unavail_torrent.update({t[2]: t for t in torrent_values if t[2] not in self.torrents})

            # Start the torrent channel checker in the first run
            if not self.is_pending_task_active(str(self.source) + "_checktor"):
                task_call = self.register_task(str(self.source) + "_checktor", LoopingCall(self._check_tor))
                self._logger.debug("Registering check torrent function")
                task_call.start(self.check_torrent_interval, now=True)

    def _on_database_updated(self, dummy_subject, dummy_change_type, dummy_infohash):
        self.database_updated = True

    def get_source_text(self):
        return str(self.channel_dict[2]) if self.channel_dict else None

    def _load_torrent(self, infohash):
        """
        function to download a torrent by infohash and call a callback afterwards
        with TorrentDef object as parameter.
        """

        def add_to_loaded(infohash_str):
            """
            function to add loaded infohash to memory
            """
            self.loaded_torrent[unhexlify(infohash_str)].callback(
                TorrentDef.load_from_memory(self.session.get_collected_torrent(unhexlify(infohash_str))))

        if infohash not in self.loaded_torrent:
            self.loaded_torrent[infohash] = defer.Deferred()

            if not self.session.has_collected_torrent(infohash):
                if self.session.has_download(infohash):
                    print "downloaded:"+hexlify(infohash)
                    return self.loaded_torrent[infohash].call# will cause error elsewehre when this is returned, not sure if this is as expected
                self.session.download_torrentfile(infohash, add_to_loaded, 0)
                print "start download:"+hexlify(infohash)
            else:
                print "have collected:"+hexlify(infohash)
                add_to_loaded(hexlify(infohash))

        return self.loaded_torrent[infohash]