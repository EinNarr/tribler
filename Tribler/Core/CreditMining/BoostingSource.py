"""
Class for boosting sources. Currently only channel source is considered.

Author(s): Bohao Zhang, based on the work of Egbert Bouman, Mihai Capota, Elric Milon, Ardhi Putra
"""
import logging
from binascii import hexlify, unhexlify

from twisted.internet import defer
from twisted.internet import reactor
from twisted.internet.task import LoopingCall

from Tribler.Core.TorrentDef import TorrentDef
from Tribler.Core.CreditMining.credit_mining_util import source_to_string
from Tribler.Core.CreditMining.BoostingTorrent import BoostingTorrent
from Tribler.Core.simpledefs import NTFY_INSERT, NTFY_TORRENTS, NTFY_UPDATE
from Tribler.dispersy.taskmanager import TaskManager
from Tribler.community.allchannel.community import AllChannelCommunity
from Tribler.community.channel.community import ChannelCommunity
from Tribler.dispersy.exception import CommunityNotFoundException

class BoostingSource(TaskManager):
    """
    Base class for boosting source. For now, it is only channel source
    """
    def __init__(self, session, source, boost_settings, torrent_insert_callback):
        super(BoostingSource, self).__init__()
        self.session = session
        self.channelcast_db = session.lm.channelcast_db

        # The dictionary of the torrents in this source, using infohash as key
        # and the Tribler.Core.CreditMining.BoostingTorrent object as value.
        self.torrents = {}

        self.source = source
        self.interval = boost_settings.source_interval
        self.max_torrents = boost_settings.max_torrents_per_source
        self.torrent_insert_callback = torrent_insert_callback

        self.enabled = True

        self.ready = False

        self.min_connection = boost_settings.min_connection_start
        self.min_channels = boost_settings.min_channels_start

        self._logger = logging.getLogger(self.__class__.__name__)
        self._logger.setLevel(1)

        self.boosting_manager = self.session.lm.boosting_manager
        # the dictionary of exact same key/value pairs as self.torrent
        self.manager_torrents = self.boosting_manager.get_torrent_list()

    def start(self):
        """
        Start operating mining for this source
        """
        d = self._load_if_ready(self.source)
        self.register_task(str(self.source) + "_load", d, value=self.source)
        self._logger.debug("Start mining on %s", source_to_string(self.source))

    def kill_tasks(self):
        """
        kill tasks on this source
        """
        self.ready = False
        self.cancel_all_pending_tasks()

    def get_torrents(self):
        return self.torrents

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

    def set_enabled(self, enabled=True):
        self.enabled = enabled

    def get_enabled(self):
        return self.enabled

    def _on_err(self, err_msg):
        self._logger.error(err_msg)

    def shutdown(self):
        """
        The function to call when removing a source.
        Aiming to delete the reference to all outer object, avoiding garbage colleting problem.
        """
        self.kill_tasks()

        for torrent in self.torrents.values():
            torrent.remove_source(self)

        self.boosting_manager = None
        self.torrent_insert_callback = None

class ChannelSource(BoostingSource):
    """
    Credit mining source from a channel.
    """
    def __init__(self, session, dispersy_cid, boost_settings, torrent_insert_callback):
        BoostingSource.__init__(self, session, dispersy_cid, boost_settings, torrent_insert_callback)

        self.channel_name = None
        self.channel_id = None

        self.channel_dict = None
        self.community = None
        self.database_updated = True

        self.check_torrent_interval = 10
        self.dispersy_cid = dispersy_cid

        self.torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)
        self.session.add_observer(self._on_database_updated, NTFY_TORRENTS, [NTFY_INSERT, NTFY_UPDATE])

        self.torrent_not_loaded = {}
        self.load_torrent_callbacks = {}

    def kill_tasks(self):
        super(ChannelSource, self).kill_tasks()

        self.session.remove_observer(self._on_database_updated)

        def errback(_, infohash):
            self._logger.info("torrent %s fetching cancelled.", unhexlify(infohash))

        for infohash in self.load_torrent_callbacks:
            deferred = self.load_torrent_callbacks[infohash]
            deferred.addErrback(errback, infohash)
            deferred.cancel()

        self.torrent_not_loaded.clear()

    def get_dispersy_cid(self):
        return self.dispersy_cid

    def _load(self, source):
        """
        @param source: the dispersy cid of a channel
        """
        dispersy = self.session.get_dispersy_instance()

        def join_community():
            """
            find the community/channel id, then join
            """
            try:
                self.community = dispersy.get_community(source, True)
                self.register_task(str(self.source) + "_get_id", reactor.callLater(1, get_channel_id))

            except CommunityNotFoundException:
                allchannelcommunity = None
                for community in dispersy.get_communities():
                    if isinstance(community, AllChannelCommunity):
                        allchannelcommunity = community
                        break

                if allchannelcommunity:
                    self.community = ChannelCommunity.init_community(dispersy, dispersy.get_member(mid=source),
                                                                     allchannelcommunity.my_member(), self.session)
                    self._logger.info("Joined channel community %s", source.encode("HEX"))
                    self.register_task(str(self.source) + "_get_id", reactor.callLater(1, get_channel_id))
                else:
                    self._logger.error("Could not find AllChannelCommunity")

        def get_channel_id():
            """
            find channel id by looking at the network
            """
            if self.community and self.community.get_channel_id():
                self.channel_id = self.community.get_channel_id()
                self.channel_name = self.community.get_channel_name()

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

    def get_name(self):
        return self.channel_name

    def _check_torrents(self):
        """
        The function to periodically check torrents in channel.
        Generate a callback chain to create BoostTorrent instance from torrent with/without metainfo in this channel.
        """
        def add_to_loaded(infohash_str):
            """
            function to add loaded infohash to memory
            """     
            self.load_torrent_callbacks[unhexlify(infohash_str)].callback(
                TorrentDef.load_from_memory(self.session.get_collected_torrent(unhexlify(infohash_str))))

        def showtorrent(torrent):
            """
            assembly torrent data, call the callback

            @param torrent: a Tribler.Core.TorrentDef object
            """
            infohash = torrent.get_infohash()
            if torrent.get_files() and infohash in self.torrent_not_loaded:
                self._logger.debug("[ChannelSource] Got torrent %s", torrent.get_name())
                self.torrents[infohash] = BoostingTorrent(torrent)
                self.torrents[infohash].add_source(self)

                self.torrents[infohash].set_enabled(self.enabled, self)

                del self.torrent_not_loaded[infohash]
                del self.load_torrent_callbacks[infohash]

                if self.torrent_insert_callback:
                    self.torrent_insert_callback(self, infohash, self.torrents[infohash])
                self.database_updated = False

        # if more torrents in this source than the limit, skip. In the new world, this is to be deleted.
        if len(self.torrents) >= self.max_torrents:
            self._logger.debug("Max torrents in ChannelSource  %s. Not adding.", self.get_channel_name())
            return

        if self.torrent_not_loaded and self.enabled:
            self._logger.debug("Not loaded #torrents : %d from %s", len(self.torrent_not_loaded), self.channel_name)
            for infohash in self.torrent_not_loaded.copy():
                # if the torrent is still being loaded in the next iteration, skip
                if infohash in self.load_torrent_callbacks:
                    continue

                # if the torrent has already appeared in another channel
                if infohash in self.manager_torrents:
                    self.torrents[infohash] = self.manager_torrents[infohash]
                    self.torrents[infohash].add_source(self)
                    continue

                self.load_torrent_callbacks[infohash] = defer.Deferred()

                # if the session does not has the metadata of this torrent
                if not self.session.has_collected_torrent(infohash):
                    # if the download task has already been added to session, skip
                    if self.session.has_download(infohash):##############need to do something if this happens
                        continue
                    else:
                        self.session.download_torrentfile(infohash, add_to_loaded, 0)
                else:
                    add_to_loaded(hexlify(infohash))

                self.load_torrent_callbacks[infohash].addCallback(showtorrent)

    def _update(self):
        if len(self.torrents) < self.max_torrents and self.database_updated:
            CHANTOR_DB = ['ChannelTorrents.channel_id', 'Torrent.torrent_id', 'infohash', '""', 'length',
                          'category', 'status', 'num_seeders', 'num_leechers', 'ChannelTorrents.id',
                          'ChannelTorrents.dispersy_id', 'ChannelTorrents.name', 'Torrent.name',
                          'ChannelTorrents.description', 'ChannelTorrents.time_stamp', 'ChannelTorrents.inserted']

            torrent_values = self.channelcast_db.getTorrentsFromChannelId(self.channel_id, True, CHANTOR_DB,
                                                                          self.max_torrents)

            # dict {key_infohash(binary):Torrent(tuples)}
            self.torrent_not_loaded.update({t[2]: t for t in torrent_values if t[2] not in self.torrents})

            # Start the torrent channel checker in the first run
            if not self.is_pending_task_active(str(self.source) + "_checktorrrents"):
                task_call = self.register_task(str(self.source) + "_checktorrrents", LoopingCall(self._check_torrents))
                self._logger.debug("Registering check torrent function")
                task_call.start(self.check_torrent_interval, now=True)

    def _on_database_updated(self, dummy_subject, dummy_change_type, dummy_infohash):
        self.database_updated = True

    def get_source_text(self):
        return str(self.channel_dict[2]) if self.channel_dict else None

    def set_enabled(self, enabled=True):
        super(ChannelSource, self).set_enabled()
        for torrent in self.torrents:
            torrent.set_enabled(enabled, self)
