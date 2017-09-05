"""
Module of Credit mining source class testing

Author(s): Bohao Zhang
"""

import binascii
import copy
import os
import time

from mock import patch

from twisted.internet.defer import inlineCallbacks

from Tribler.Core.CreditMining.BoostingSource import BoostingSource, ChannelSource
from Tribler.Core.CreditMining.BoostingManager import BoostingSettings, BoostingManager
from Tribler.Core.DownloadConfig import DefaultDownloadStartupConfig
from Tribler.Core.leveldbstore import LevelDbStore
from Tribler.Core.RemoteTorrentHandler import RemoteTorrentHandler
from Tribler.Core.TorrentDef import TorrentDef
from Tribler.dispersy.util import blocking_call_on_reactor_thread
from Tribler.Test.common import TORRENT_UBUNTU_FILE, TORRENT_UBUNTU_FILE_INFOHASH
from Tribler.Test.Core.base_test_channel import BaseTestChannel
from Tribler.Test.Core.CreditMining.mock_creditmining import MockSearchCommunity, MockLibtorrentDownloadImpl


class TestBoostingSource(BaseTestChannel):
    """
    Test class for channel boosting source
    """
    def __init__(self, *argv, **kwargs):
        super(TestBoostingSource, self).__init__(*argv, **kwargs)

    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def setUp(self, autoload_discovery=True):
        yield super(TestBoostingSource, self).setUp()

        self.set_boosting_settings()

        # cheating all dependency checks
        self.session.get_libtorrent = lambda: True
        self.session.get_torrent_checking = lambda: True
        self.session.get_dispersy = lambda: True
        self.session.get_torrent_store = lambda: True
        self.session.get_enable_torrent_search = lambda: True
        self.session.get_enable_channel_search = lambda: True
        self.session.get_megacache = lambda: True

        # self.bsettings = BoostingSettings(self.session)
        self.boosting_manager = BoostingManager(self.session, self.bsettings)
        self.boosting_source = BoostingSource(self.session, 'fakedispersycid', self.bsettings,
                                              self.boosting_manager.on_torrent_insert)

    def set_boosting_settings(self):
        """
        set settings in credit mining
        """
        self.bsettings = BoostingSettings(self.session)
        self.bsettings.credit_mining_path = os.path.join(self.session_base_dir, "credit_mining")
        self.bsettings.load_config = True
        self.bsettings.check_dependencies = True
        self.bsettings.min_connection_start = -1
        self.bsettings.min_channels_start = -1

        self.bsettings.max_torrents_active = 8
        self.bsettings.max_torrents_per_source = 5

        self.bsettings.tracker_interval = 5
        self.bsettings.initial_tracker_interval = 5
        self.bsettings.logging_interval = 30
        self.bsettings.initial_logging_interval = 3

        self.bsettings.swarm_interval = 1
        self.bsettings.source_interval = 1
        self.bsettings.initial_swarm_interval = 1
        self.bsettings.max_torrents_active = 1
        self.bsettings.max_torrents_per_source = 1
        self.bsettings.share_mode_target = 1

    def setUpPreSession(self):
        super(TestBoostingSource, self).setUpPreSession()

        self.config.set_torrent_checking(True)
        self.config.set_dispersy(True)
        self.config.set_libtorrent(True)

        # using dummy dispersy
        self.config.set_dispersy(False)

    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def tearDown(self, annotate=True):
        DefaultDownloadStartupConfig.delInstance()
        self.boosting_source.kill_tasks()
        self.boosting_manager.shutdown()

        yield super(TestBoostingSource, self).tearDown(annotate=annotate)

    def test_start(self):
        """
        test for starting operating mining from a boosting source
        """
        self.boosting_source.start()
        tasks = self.boosting_source._pending_tasks
        self.assertTrue('fakedispersycid_load' in tasks, 'Loading task is not registered.')
        self.assertTrue('check_availability fakedispersycid' in tasks, 'Checking availablity task is not registered.')

    def test_kill_tasks(self):
        """
        test for killing tasks in a boosting source
        """
        # it is already been called when tearDown
        pass

    def test_load_if_ready(self):
        """
        test for loading source iff the overall system is ready
        """
        # create fake mining source
        source = 'fakedispersycid'

        # mock isinstance in order to mock SearchCommunity
        copy_isinstance = copy.copy(isinstance)

        def fake_isinstance(this, that):
            return copy_isinstance(this, MockSearchCommunity) or copy_isinstance(this, that)
        self.session.lm.dispersy._communities['search'] = MockSearchCommunity()

        self.boosting_source.min_connection = 0

        # mock _load
        called_source = list()

        def fake_load(source):
            called_source.append(source)
        self.boosting_source._load = fake_load

        # start testing
        with patch('__builtin__.isinstance', fake_isinstance):
            self.boosting_source._load_if_ready(source)
            self.assertTrue(self.boosting_source._pending_tasks,
                            'New system checking task is not registered when system is not ready.')
            self.session.lm.dispersy._communities['search'].nr_connections = 1
            delayed_call = self.boosting_source._pending_tasks['fakedispersycid_check_sys']
            delayed_call.func(*delayed_call.args)
            delayed_call.reset(0)
            self.assertTrue(called_source, 'Deferred is not fired when system is ready')

    def test_load(self):
        """
        no implementation of _load in base source class
        """
        self.boosting_source._load('fakedispersycid')

    def test_update(self):
        """
        no implementation of _upload in base source class
        """
        self.boosting_source._update()

    def test_get_source_text(self):
        """
        test for returning 'raw' source
        """
        # start testing
        self.assertEqual(self.boosting_source.get_source_text(), 'fakedispersycid', 'Not returning "raw" source')

    def test_on_err(self):
        """
        test for error logger
        """
        self.boosting_source._on_err('fakeerrormessage')

    def test_check_available(self):
        """
        test for removing the idle torrent from the list
        """
        self.session.lm.boosting_manager = self.boosting_manager
        # create fake torrents
        info_hash1 = 'fakeinfohash1' # idle
        info_hash2 = 'fakeinfohash2' # not idle
        info_hash3 = 'fakeinfohash3' # not in boosting manager
        info_hash4 = 'fakeinfohash4' # in boosting manager not downloading
        info_hash5 = 'fakeinfohash5' # have download infomation in boosting source but not in boosting manager
        # TODO no idea what might lead to case 5 in real life
        infohash6 = 'fakeinfohash6' # newly added blacklisted torrent
        infohash7 = 'fakeinfohash7' # to be revocered blacklisted torrent

        self.boosting_source.torrents[info_hash1] = {'time': {'last_activity': 1},
                                                     'download': MockLibtorrentDownloadImpl(info_hash1)}
        self.boosting_source.torrents[info_hash2] = {'time': {'last_activity': time.time()},
                                                     'download': MockLibtorrentDownloadImpl(info_hash2)}
        self.boosting_source.torrents[info_hash3] = {'time': {'last_activity': 1},
                                                     'download': MockLibtorrentDownloadImpl(info_hash3)}
        self.boosting_source.torrents[info_hash4] = {'time': {'last_activity': 1}}
        self.boosting_source.torrents[info_hash5] = {'time': {'last_activity': 1},
                                                     'download': MockLibtorrentDownloadImpl(info_hash5)}

        self.boosting_manager.torrents[info_hash1] = {'time': {'last_activity': 1},
                                                      'download': MockLibtorrentDownloadImpl(info_hash1)}
        self.boosting_manager.torrents[info_hash2] = {'time': {'last_activity': time.time()},
                                                      'download': MockLibtorrentDownloadImpl(info_hash2)}
        self.boosting_manager.torrents[info_hash4] = {'time': {'last_activity': 1}}
        self.boosting_manager.torrents[info_hash5] = {'time': {'last_activity': 1}}

        self.boosting_source.blacklist_torrent[infohash6] = time.time()
        self.boosting_source.blacklist_torrent[infohash7] = 1

        # mock stop_download()
        stopped_downloads = list()

        def fake_stop_download(info_hash, remove_torrent=False, reason="N/A"):
            stopped_downloads.append(info_hash)
        self.boosting_manager.stop_download = fake_stop_download

        # start testing
        self.boosting_source.check_available()
        self.assertTrue(info_hash1 in stopped_downloads, 'Idle torrent is not removed.')
        self.assertFalse(info_hash2 in stopped_downloads, 'Active torrent is removed unexpectedly.')
        self.assertFalse(info_hash5 in self.boosting_manager.torrents,
                         'Stopped torrent is not removed form torrent list.')


class TestChannelSource(TestBoostingSource):
    """
    Test class for channel source
    """
    def __init__(self, *argv, **kwargs):
        super(TestChannelSource, self).__init__(*argv, **kwargs)
        
    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def setUp(self, autoload_discovery=True):
        yield super(TestChannelSource, self).setUp()
        self.boosting_source = ChannelSource(self.session, 'fakedispersycid', self.bsettings,
                                             self.boosting_manager.on_torrent_insert)

    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def tearDown(self, annotate=True):
        yield super(TestChannelSource, self).tearDown(annotate=annotate)

    def test_kill_tasks(self):
        """
        test for killing tasks in a channel source
        """
        # it is already been called when tearDown
        pass
    
    @blocking_call_on_reactor_thread
    def test_load(self):
        """
        test for finding and joining a channel by id
        """
        # create_fake_allchannel_community
        self.create_fake_allchannel_community()

        # create fake channel
        dispersy_cid_hex = "abcd" * 9 + "0012"
        dispersy_cid = binascii.unhexlify(dispersy_cid_hex)
        self.channel_id = self.insert_channel_in_db(dispersy_cid_hex.decode('hex'), 42,
                                                    'Fake Channel', 'Fake channel description')

        # start testing
        self.boosting_source._load(dispersy_cid)
        self.assertTrue('fakedispersycid_join_comm' in self.boosting_source._pending_tasks,
                        'Task to join community is not registered.')
        join_comunity = self.boosting_source._pending_tasks['fakedispersycid_join_comm'].func
        self.boosting_source.cancel_all_pending_tasks()
        join_comunity()
        self.assertTrue('fakedispersycid_get_id' in self.boosting_source._pending_tasks,
                        'Task to find channel ID is not registered when the channel community do not exist.')
        self.boosting_source.cancel_all_pending_tasks()
        join_comunity()  # call a second time, at this time the channel community is set up
        self.assertTrue('fakedispersycid_get_id' in self.boosting_source._pending_tasks,
                        'Task to find channel ID is not registered when the channel community has existed.')
        get_channel_id = self.boosting_source._pending_tasks['fakedispersycid_get_id'].func
        self.boosting_source.community.cancel_all_pending_tasks()
        get_channel_id()
        self.assertTrue(self.boosting_source.ready, 'A loaded channel source is not set to "ready".')
        self.assertTrue('fakedispersycid_update' in self.boosting_source._pending_tasks,
                        'Task to update channel infomation is not registered.')

        # test when channel community and allchannelcommunity cannot be found
        self.session.lm.dispersy._communities = dict()
        join_comunity()

        # test when channel community id cannot be be got
        self.boosting_source.cancel_all_pending_tasks()
        self.boosting_source.community._channel_id = 0
        get_channel_id()

        # additional tear down procedure
        self.boosting_source.community.cancel_all_pending_tasks()

    def test_check_tor(self):
        """
        test for periodically checking torrents in channel
        """
        # initialize TorrentDef instance
        tdef = TorrentDef.load(TORRENT_UBUNTU_FILE)

        # add torrent info from test_update() to unavail_torrent
        self.boosting_source.unavail_torrent[TORRENT_UBUNTU_FILE_INFOHASH] = \
            [1, 1, TORRENT_UBUNTU_FILE_INFOHASH, u'', 1150844928, u'other', u'unknown', None, None, 1, 1,
             TORRENT_UBUNTU_FILE, TORRENT_UBUNTU_FILE, None, 1460000000, 1504551197]
        unavail_torrent_copy = copy.deepcopy(self.boosting_source.unavail_torrent)
        
        # mock torrent database in launchManyCore, same as test_load_torrent
        self.session.lm.torrent_store = LevelDbStore(self.session.get_torrent_store_dir())

        self.session.lm.rtorrent_handler = RemoteTorrentHandler(self.session)
        self.session.lm.rtorrent_handler.initialize()

        # start testing
        self.boosting_source._check_tor()
        show_torrent = self.boosting_source.loaded_torrent[TORRENT_UBUNTU_FILE_INFOHASH].callbacks[0][0][0]

        show_torrent(tdef)
        self.assertTrue(TORRENT_UBUNTU_FILE_INFOHASH in self.boosting_source.torrents,
                        'Torrent information is not added to the ChannelSource instance.')
        self.assertFalse(self.boosting_source.unavail_torrent,
                         'Torrent is not removed from the unavailable torrents list after the information is updated to'
                         ' the ChannelSource instance.')
        self.assertFalse(self.boosting_source.database_updated,
                         'Database_updated flag is not set to false after updating the ChannelSource instance.')
        # test when torrent is blacklisted
        self.boosting_manager.cancel_all_pending_tasks()
        self.boosting_source.unavail_torrent = copy.deepcopy(unavail_torrent_copy)
        self.boosting_source.blacklist_torrent[TORRENT_UBUNTU_FILE_INFOHASH] = None
        show_torrent(tdef)
        self.assertFalse(self.boosting_source.unavail_torrent,
                         'Torrent is not removed from the unavailable torrents list when it is blacklisted.')
        self.boosting_source.blacklist_torrent = dict()
        # test when max torrents in the source is reached
        self.boosting_manager.cancel_all_pending_tasks()
        self.boosting_source.unavail_torrent = copy.deepcopy(unavail_torrent_copy)
        self.boosting_source.max_torrents = 0
        show_torrent(tdef)
        self.assertFalse(self.boosting_source.unavail_torrent,
                         'Torrent is not removed from the unavailable torrents list when max torrents is reached in '
                         'this channel source.')

    @blocking_call_on_reactor_thread
    def test_update(self):
        """
        test for update info in the channel source from the dispersy channel
        """
        # create fake channel and insert a torrent into the channel
        self.create_fake_allchannel_community()
        dispersy_cid_hex = "abcd" * 9 + "0012"
        dispersy_cid = binascii.unhexlify(dispersy_cid_hex)
        self.channel_id = self.insert_channel_in_db(dispersy_cid_hex.decode('hex'), 42,
                                                    'Fake Channel', 'Fake channel description')

        tdef = TorrentDef.load(TORRENT_UBUNTU_FILE)
        torrent_list = [[self.channel_id, 1, 1, TORRENT_UBUNTU_FILE_INFOHASH, 1460000000, TORRENT_UBUNTU_FILE,
                         tdef.get_files_with_length(), tdef.get_trackers_as_single_tuple()]]
        self.insert_torrents_into_channel(torrent_list)

        # mock _check_tor()
        self.boosting_source._check_tor = lambda: None

        # load the info from channel to boosting source, same as test_load()
        self.boosting_source._load(dispersy_cid)
        join_comunity = self.boosting_source._pending_tasks['fakedispersycid_join_comm'].func
        self.boosting_source.cancel_all_pending_tasks()
        join_comunity()
        get_channel_id = self.boosting_source._pending_tasks['fakedispersycid_get_id'].func
        self.boosting_source.community.cancel_all_pending_tasks()
        
        # start testing, note that get_channel_id() calls _update() inside
        self.assertFalse(self.boosting_source.unavail_torrent,
                         'The unavailable torrents dictionary is not empty when on start.')
        get_channel_id()
        self.assertTrue(TORRENT_UBUNTU_FILE_INFOHASH in self.boosting_source.unavail_torrent,
                        'The new torrent is not added to unavailable torrents dictionary.')
        self.assertTrue('fakedispersycid_checktor' in self.boosting_source._pending_tasks,
                        'Task to periodically check torrents in the channel is not registered.')

        # additional tear down procedure
        self.boosting_source.community.cancel_all_pending_tasks()
        
    def test_on_database_updated(self):
        """
        test for setting flag when database is updated
        """
        self.boosting_source.database_updated = False
        self.boosting_source._on_database_updated("fakesubject", "fakechangetype", "fakeinfohash")
        self.assertTrue(self.boosting_source.database_updated, "Database updated flag is not set.")

    @blocking_call_on_reactor_thread
    def test_get_source_text(self):
        """
        test for getting the name of the channel source
        """
        # copyed result from test_load()
        self.boosting_source.channel_dict = (1, TORRENT_UBUNTU_FILE_INFOHASH, u'Fake Channel',
                                             u'Fake channel description', 0, 0, 0, 0, 1504459471, False)
        
        # start testing
        self.assertEqual(self.boosting_source.get_source_text(), 'Fake Channel',
                         'Cannot get the name of the channel source correctly.')

    @blocking_call_on_reactor_thread
    def test_load_torrent(self):
        """
        test for downloading a torrent by infohash and calling a callback afterwards
        with TorrentDef object as parameter.
        """
        # mock torrent database in launchManyCore
        self.session.lm.torrent_store = LevelDbStore(self.session.get_torrent_store_dir())

        self.session.lm.rtorrent_handler = RemoteTorrentHandler(self.session)
        self.session.lm.rtorrent_handler.initialize()

        # mock TorrentDef.load_from_memory, skipping the verification of loading the torrent from memory
        @staticmethod
        def fake_load_from_memory(_):
            pass

        # start testing
        self.boosting_source._load_torrent(TORRENT_UBUNTU_FILE_INFOHASH)
        self.assertTrue(TORRENT_UBUNTU_FILE_INFOHASH in self.boosting_source.loaded_torrent,
                        'Deferred to load torrent is not registered.')
        add_to_loaded = self.session.lm.rtorrent_handler.torrent_callbacks[TORRENT_UBUNTU_FILE_INFOHASH].pop()
        with patch('Tribler.Core.TorrentDef.TorrentDef.load_from_memory', fake_load_from_memory):
            add_to_loaded(binascii.hexlify(TORRENT_UBUNTU_FILE_INFOHASH))

        # test when download already exist
        self.boosting_source.loaded_torrent = dict()
        self.session.has_download = lambda _: True
        self.boosting_source._load_torrent(TORRENT_UBUNTU_FILE_INFOHASH)
        self.assertTrue(TORRENT_UBUNTU_FILE_INFOHASH in self.boosting_source.loaded_torrent,
                        'Downloaded torrrent is not registered.')
