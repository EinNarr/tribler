"""
Supported boosting policy.

Author(s): Bohao Zhang, based on the work of Egbert Bouman, Mihai Capota, Elric Milon, Ardhi Putra
"""


import os
import shutil
import psutil

from binascii import hexlify, unhexlify

from twisted.internet.task import LoopingCall
from twisted.internet import reactor

from Tribler.Core.CreditMining.BoostingSource import ChannelSource
from Tribler.Core.CreditMining.defs import SAVED_ATTR, CREDIT_MINING_FOLDER_DOWNLOAD
from Tribler.Core.DownloadConfig import DownloadStartupConfig, DefaultDownloadStartupConfig
from Tribler.dispersy.taskmanager import TaskManager
from Tribler.Core.simpledefs import NTFY_TORRENTS, NTFY_CHANNELCAST, NTFY_UPDATE
from Tribler.Core.CreditMining.credit_mining_util import source_to_string, string_to_source, validate_source_string

from Tribler.Core.CreditMining.BoostingPolicy import RandomPolicy, VitalityPolicy
from Tribler.Core.exceptions import OperationNotPossibleAtRuntimeException

class BoostingSettings(object):
    """
    This class contains settings used by the boosting manager
    """
    def __init__(self, policy=VitalityPolicy, load_config=True):
        # Configurable parameter (changeable in runtime -plus sources-)
        self.max_torrents_active = 20
        self.max_torrents_per_source = 10
        self.source_interval = 100
        self.swarm_interval = 100

        # Can't be changed on runtime
        self.tracker_interval = 200
        self.logging_interval = 60
        self.share_mode_target = 3
        self.policy = policy

        # Non-Configurable
        self.initial_logging_interval = 20
        self.initial_tracker_interval = 25
        self.initial_swarm_interval = 30
        self.min_connection_start = 5
        self.min_channels_start = 100
        self.credit_mining_path = os.path.join(DefaultDownloadStartupConfig.getInstance().get_dest_dir(),
                                               CREDIT_MINING_FOLDER_DOWNLOAD)
        self.load_config = load_config

        # whether we want to check dependencies of BoostingManager
        self.check_dependencies = True
        self.auto_start_source = True

        # in seconds
        self.time_check_interval = 2
        self.timeout_torrent_activity = 240

class BoostingManager(TaskManager):
    """
    Class to manage all the credit mining activities
    """

    def __init__(self, session, settings=None):
        super(BoostingManager, self).__init__()

        self._logger = logging.getLogger(self.__class__.__name__)

        # the dictory of BoostingSource instances, using hexlified channel id as key
        self.boosting_sources = {}
        self.boosting_sources_enabled = {}
        # the dictory of BoostingTorrent instances, storing all torrents from all sources, using infohash as key
        self.torrents = {}
        self.torrents_enabled = {}
        self.torrents_boosting = {}

        self.session = session

        self.settings = settings or BoostingSettings()

        if self.settings.load_config:
            self.load_config()

        self.policy = self.settings.policy(self.session, self.torrents_enabled, self.torrents_boosting, self.settings.max_torrents_active)

        if self.settings.check_dependencies:
            assert self.session.config.get_libtorrent_enabled()
            assert self.session.config.get_torrent_checking_enabled()
            assert self.session.config.get_dispersy_enabled()
            assert self.session.config.get_torrent_store_enabled()
            assert self.session.config.get_torrent_search_enabled()
            assert self.session.config.get_channel_search_enabled()
            assert self.session.config.get_megacache_enabled()

        self.channelcast_db = self.session.open_dbhandler(NTFY_CHANNELCAST)
        self.torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)

        # Check the existance of credit mining directory.
        # If there is, it could be the result of a crash, so delete it.
        if os.path.exists(self.settings.credit_mining_path):
            shutil.rmtree(self.settings.credit_mining_path, ignore_errors=True)
        os.makedirs(self.settings.credit_mining_path)

        # self.session.add_observer(self.on_torrent_notify, NTFY_TORRENTS, [NTFY_UPDATE])

        # The looping call to periodically add/remove torrent info/from the boosting list
        self.register_task("CreditMining_select", LoopingCall(self._select_torrent),
                           self.settings.initial_swarm_interval, interval=self.settings.swarm_interval)

    def get_torrent_list(self):
        return self.torrents

    def get_boosting_list(self):
        return self.torrents_boosting

    def shutdown(self):
        """
        Shutting down boosting manager. It also stops and remove all the sources.
        """
        self.save_config()
        self._logger.info("Shutting down boostingmanager")

        self.cancel_all_pending_tasks()

        for sourcekey in self.boosting_sources.keys():
            self.remove_source(sourcekey, recheck_torrent=False)
        for torrent in self.torrents_boosting.values():
            self.stop_download(torrent, reason="removing source")

        # make sure that this object is not referred by anything else than LaunchManyCore object
        # if it is referred by BoostingSource or BoostingTorrents, it may cause memory leak

        # remove credit mining downloaded data
        shutil.rmtree(self.settings.credit_mining_path, ignore_errors=True)

    def set_enable_mining(self, source, enabled=True, force_restart=False):#not done
        """
        Dynamically enable/disable mining source.

        Future: this is to be deleted when optimized policy and swarm investigating algorithm are implemented
        In the new world, all the torrents from all the channels would be added to this boosting manager.
        So there would be no need to specify the sources.
        @param source: BoostingSource
        """
        source.set_enabled(enabled)
        self.boosting_sources_enabled[source.get_dispersy_cid()] = source

        if enabled:
            self.torrents_enabled.update(source.get_torrents())
        else:
            for infohash, torrent in source.get_torrents().iteritems():
                if not torrent.get_enabled():
                    del self.torrents_enabled[infohash]
                    self.stop_download(torrent, reason="disabling source")

        self._logger.info("Set mining source %s %s", source.get_name(), enabled)

        if force_restart:
            self._select_torrent()

    def add_source(self, source):
        """
        add new source into the boosting manager
        """
        if source not in self.boosting_sources:
            args = (self.session, source, self.settings, self.on_torrent_insert)

            if len(source) == 20:
                self.boosting_sources[source] = ChannelSource(*args)
            else:
                self._logger.error("Cannot add unknown source %s", source)
                return

            if self.settings.auto_start_source:
                self.boosting_sources[source].start()

            self._logger.info("Added source %s", self.boosting_sources[source].get_name())
        else:
            self._logger.info("Already have source %s", self.boosting_sources[source].get_name())

    def remove_source(self, source_key, recheck_torrent=True):
        """
        remove source by stop the downloading and remove its metainfo for all its swarms
        """
        if source_key in self.boosting_sources.keys():
            source = self.boosting_sources.pop(source_key)
            if source_key in self.boosting_sources_enabled:
                self.boosting_sources_enabled.pop(source_key)
            self._logger.info("Removed source %s", source.get_name())

            if recheck_torrent:
                for torrent in source.get_torrents().values():
                    if not torrent.get_source() and torrent.get_infohash() in self.torrents:
                        self.torrents.pop(torrent.get_infohash())
                    if not torrent.get_enabled() and torrent.get_infohash() in self.torrents_enabled:
                        self.torrents_enabled.pop(torrent.get_infohash())
                        if torrent.get_infohash() in self.torrents_boosting:
                            self.stop_download(torrent, reason="removing source")
                            self.torrents_boosting.pop(torrent.get_infohash())

            source.shutdown()

    def on_torrent_insert(self, source, infohash, torrent):
        """
        This function called when a source is finally determined. Fetch some torrents from it,
        then insert it into our data
        """
        self._logger.debug("torrent %s added from %s", torrent.get_name(), source.get_name())

        if source.get_dispersy_cid() in self.boosting_sources:
            self.torrents[infohash] = torrent
        else:
            self._logger.info("Dropping torrent insert from removed source: %s", repr(torrent.getname()))
        
        if source.get_dispersy_cid() in self.boosting_sources_enabled:
            for torrent in source.get_torrents().values():
                torrent.set_enabled(True, source)
                self.torrents_enabled[torrent.get_infohash()] = torrent

    def on_states_callback(self, states_list):
        """
        The function called from LaunchManyCore when the download state is updated, basically called every second.
        @param states_list: a list of Tribler.Core.DownloadState.DownloadState objects
        """
        for download_state in states_list:
            infohash = download_state.get_download().get_def().get_infohash()
            if infohash in self.torrents:
                self.torrents[infohash].update_downloadstate(download_state)

########################################
    def __bdl_callback(self, ds):##############what is this
        '''
        #TODO(Bohao) function unknown, probably removing all seeding peers from peer list.
        '''
        ihash_str = ds.get_download().get_def().get_infohash().encode('hex')

        #TODO(Bohao) see the question in get_peerlist. Does have work?
        #TODO(Bohao) any possible that it don't have 'have'?
        peers = [x for x in ds.get_peerlist() if any(x['have']) and not
                 x['ip'].startswith("127.0.0")]

        ds.get_peerlist = lambda: peers

        availability = ds.get_availability()
        ihash = unhexlify(ihash_str)

        if ihash in self.torrents.keys():
            self.torrents[ihash]['availability'] = availability
            self.torrents[ihash]['livepeers'] = peers
            for peer in self.torrents[ihash]['livepeers']:
                self.__insert_peer(ihash, peer['ip'], peer['port'], peer)

        return 1.0, True
#################################################

    def start_download(self, torrent):
        """
        Start downloading a particular torrent and add it to download list in Tribler
        @param torrent: BoostingTorrent object
        """
        dscfg = DownloadStartupConfig()
        dscfg.set_dest_dir(self.settings.credit_mining_path)
        dscfg.set_safe_seeding(False)
        dscfg.set_seeding_mode('forever')

        if self.session.lm.download_exists(torrent.get_infohash()):
            self._logger.debug("Download already exists. Restarting %s",
                               torrent.get_name())
            self.session.get_download(torrent.get_infohash()).restart()
            return

        self._logger.info("Starting %s", torrent.get_name())

        torrent.start_download(self.session, dscfg)
        # torrent.get_download().set_state_callback(self.__bdl_callback, True)############ What does this do

    def stop_download(self, torrent, reason="N/A"):
        """
        Stopping torrent that currently downloading
        @param torrent: BoostingTorrent object
        """
        self._logger.info("Stopping %s, reason : %s", torrent.get_name(), reason)
        torrent.stop()

    def torrent_is_boosting(self, infohash):
        """
        Return whether a torrent is being boosted by this manager
        """
        return infohash in self.torrents_boosting

    def handover_download(self, infohash):
        """
        Called when a torrent being boosted is also manually required by the user
        Remove the torrent from the boosting list to hand over control
        """
        if infohash in self.torrents_boosting:
            del self.torrents_boosting[infohash]

    def _select_torrent(self):
        """
        Function to select which torrent in the torrent list will be downloaded in the
        next iteration. It depends on the source and applied policy
        #TODO(Bohao) not qutie sure about the logic here.
        If preloaded and not finished download -> do nothing
        If preloaded and not started -> start download
        If preloaded and finished download -> stop download and enter archive mode???
        If not preloaded not duplicate and enabled, add it to the set need to be judged by policy
        """
        self._select_channel() # Not implemented yet. Final goal: always select all the torrents.
        ##################
        self.record_test_data()
        ##################
        if self.settings.policy:
            # Determine which torrent to start and which to stop.
            torrents_start, torrents_stop = self.policy.apply()

            for torrent in torrents_stop:
                self.stop_download(torrent, reason="by policy")
                del self.torrents_boosting[torrent.get_infohash()]
            for torrent in torrents_start:
                self.torrents_boosting[torrent.get_infohash()] = torrent
                self.start_download(torrent)

            self._logger.info("Selecting from %s torrents %s start download", len(self.torrents_enabled), len(torrents_start))

    def record_test_data(self):
        pass
        # ltsession = self.session.lm.ltmgr.get_session()
        # status = ltsession.status()

        # up_speed = status.upload_rate
        # down_speed = status.download_rate
        # up_total = status.total_upload
        # down_total = status.total_download
        # up_speed_payload = status.payload_upload_rate
        # down_speed_payload = status.payload_download_rate
        # up_total_payload = status.total_payload_upload
        # down_total_payload = status.total_payload_download

        # cpu = psutil.Process().cpu_percent()
        # rss = psutil.Process().memory_full_info().rss
        # pss = psutil.Process().memory_full_info().pss
        # swap = psutil.Process().memory_full_info().swap
        # memory_percent = psutil.Process().memory_percent(memtype='pss')

        # fields = [self.timestamp, up_speed, down_speed, up_total, down_total,
        #           up_speed_payload, down_speed_payload, up_total_payload, down_total_payload,
        #           cpu, rss, pss, swap, memory_percent]

        # self.timestamp += self.settings.swarm_interval

        # with open(os.path.join(self.session.config.get_state_dir(), "test_data.csv"), 'a') as t:
        #     writer = csv.writer(t)
        #     writer.writerow(fields)

    def _select_channel(self):
        pass

    def load_config(self):
        """
        load config in file configuration and apply it to manager
        """
        self._logger.info("Loading config file from session configuration")

        def _add_sources(values):
            """
            adding sources in configuration file
            """
            for boosting_source in values:
                boosting_source = validate_source_string(boosting_source)
                self.add_source(boosting_source)

        def _set_enable_boosting(values, enabled):
            """
            set disable/enable source
            """
            for boosting_source in values:
                boosting_source = validate_source_string(boosting_source)
                self.set_enable_mining(self.boosting_sources[boosting_source], enabled=enabled)

        # set policy(not instantiated)
        self.settings.policy = self.session.config.get_credit_mining_policy(True)

        # overwrite the BoostingSettings with the ones in Tribler session configurations.
        for k in SAVED_ATTR:
            setattr(self.settings, k, getattr(self.session.config, "get_credit_mining_%s" %k)())

        for k, val in self.session.config.get_credit_mining_sources().items():
            if k is "boosting_sources":
                self.register_task("BoostingManger_load_sources", reactor.callLater(1, _add_sources, val))
            elif k is "boosting_enabled":
                self.register_task("BoostingManger_load_enabled_sources", reactor.callLater(1, _set_enable_boosting, val, True))
            elif k is "boosting_disabled":
                self.register_task("BoostingManger_load_disabled_sources", reactor.callLater(1, _set_enable_boosting, val, False))

    def save_config(self):
        """
        save the environment parameters in config file
        """
        pass
        # for k in SAVED_ATTR:
        #     try:
        #         getattr(self.session.config, "set_credit_mining_%s" % k)(getattr(self.settings, k))
        #     except OperationNotPossibleAtRuntimeException:
        #         # some of the attribute can't be changed in runtime. See lm.sessconfig_changed_callback
        #         self._logger.debug("Cannot set attribute %s. Not permitted in runtime", k)

        # archive_sources = []
        # lboosting_sources = []
        # flag_enabled_sources = []
        # flag_disabled_sources = []
        # for boosting_source_name, boosting_source in \
        #         self.boosting_sources.iteritems():

        #     bsname = source_to_string(boosting_source_name)

        #     lboosting_sources.append(bsname)
        #     if boosting_source.enabled:
        #         flag_enabled_sources.append(bsname)
        #     else:
        #         flag_disabled_sources.append(bsname)

        #     if boosting_source.archive:
        #         archive_sources.append(bsname)

        # self.session.config.set_credit_mining_sources(lboosting_sources, CONFIG_KEY_SOURCELIST)
        # self.session.config.set_credit_mining_sources(flag_enabled_sources, CONFIG_KEY_ENABLEDLIST)
        # self.session.config.set_credit_mining_sources(flag_disabled_sources, CONFIG_KEY_DISABLEDLIST)
        # self.session.config.set_credit_mining_sources(archive_sources, CONFIG_KEY_ARCHIVELIST)

        # self.session.config.write()