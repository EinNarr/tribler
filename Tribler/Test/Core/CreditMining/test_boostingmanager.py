"""
Module of Credit mining boosting manager, the core class for credit minging

Author(s): Bohao Zhang
"""
import os
import copy

from binascii import hexlify, unhexlify

from twisted.internet import defer
from twisted.internet.task import LoopingCall
from twisted.internet.defer import Deferred, inlineCallbacks

from Tribler.Test.test_as_server import TestAsServer
from Tribler.Test.Core.CreditMining.mock_creditmining import MockLtTorrent, MockChannelSource, MockLtSession, MockBoostingPolicy, MockLibtorrentDownloadImpl, MockTorrentDef, MockDownloadState
from Tribler.Test.common import TORRENT_UBUNTU_FILE, TORRENT_UBUNTU_FILE_INFOHASH, TESTS_DATA_DIR
from Tribler.Test.Core.base_test_channel import BaseTestChannel
from Tribler.Core.CreditMining.defs import SAVED_ATTR
from Tribler.Core.TorrentDef import TorrentDef
from Tribler.Core.exceptions import OperationNotPossibleAtRuntimeException
from Tribler.Core.simpledefs import DLSTATUS_SEEDING, NTFY_TORRENTS, NTFY_UPDATE, NTFY_CHANNELCAST
from Tribler.Core.DownloadConfig import DefaultDownloadStartupConfig
from Tribler.dispersy.util import blocking_call_on_reactor_thread
from Tribler.Core.CreditMining.BoostingManager import BoostingManager, BoostingSettings


class TestBoostingManager(BaseTestChannel):
    """
    Base class to test base credit mining function.
    """

    def __init__(self, *argv, **kwargs):
        super(TestBoostingManager, self).__init__(*argv, **kwargs)
        self.tdef = TorrentDef.load(TORRENT_UBUNTU_FILE)
        self.channel_id = 0
        # self._check_source_lc = None
        # self._check_torrents_lc = None

    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def setUp(self, autoload_discovery=True):
        yield super(TestBoostingManager, self).setUp()

        self.set_boosting_settings()

        # self.session.lm.ltmgr.get_session().find_torrent = lambda _: MockLtTorrent()

        self.boosting_manager = BoostingManager(self.session, self.bsettings)
        #setting up a mock channel source
        self.mock_channel_source = MockChannelSource()

        # self.session.lm.boosting_manager = self.boosting_manager

    def set_boosting_settings(self):
        """
        set settings in credit mining
        """
        self.bsettings = BoostingSettings(self.session)
        self.bsettings.credit_mining_path = os.path.join(self.session_base_dir, "credit_mining")
        self.bsettings.load_config = False
        self.bsettings.check_dependencies = False
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
        super(TestBoostingManager, self).setUpPreSession()

        self.config.set_torrent_checking(True)
        # self.config.set_megacache(True)
        self.config.set_dispersy(True)
        # self.config.set_torrent_store(True)
        # self.config.set_enable_torrent_search(True)
        # self.config.set_enable_channel_search(True)
        self.config.set_libtorrent(True)

        #using dummy dispersy
        self.config.set_dispersy(False)

    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def tearDown(self, annotate=True):
        DefaultDownloadStartupConfig.delInstance()
        self.boosting_manager.shutdown()

        yield super(TestBoostingManager, self).tearDown(annotate=annotate)

    # def check_torrents(self, src, target=1):
    #     """
    #     Check if a specified number of torrent has been added to the passed source.
    #     """
    #     defer_param = defer.Deferred()

    #     def do_check():
    #         src_obj = self.boosting_manager.get_source_object(src)
    #         if src_obj and len(src_obj.torrents) >= target:

    #             def _get_tor_dummy(_, keys=123, include_mypref=True):
    #                 """
    #                 function to emulate get_torrent in torrent_db
    #                 """
    #                 return {'C.torrent_id': 93, 'category': u'Compressed', 'torrent_id': 41,
    #                         'infohash': src_obj.torrents.keys()[0], 'length': 1150844928, 'last_tracker_check': 10001,
    #                         'myDownloadHistory': False, 'name': u'ubuntu-15.04-desktop-amd64.iso',
    #                         'num_leechers': 999, 'num_seeders': 123, 'status': u'unknown', 'tracker_check_retries': 0}
    #             self.boosting_manager.torrent_db.getTorrent = _get_tor_dummy
    #             self.session.notifier.notify(NTFY_TORRENTS, NTFY_UPDATE, src_obj.torrents.keys()[0])

    #             # log it
    #             self.boosting_manager.log_statistics()

    #             self._check_torrents_lc.stop()
    #             self._check_torrents_lc = None
    #             defer_param.callback(src)

    #     self._check_torrents_lc = LoopingCall(do_check)
    #     self._check_torrents_lc.start(1, now=True)

    #     return defer_param

    # def check_source(self, src):
    #     """
    #     function to check if a source is ready initializing
    #     """
    #     defer_param = defer.Deferred()

    #     def do_check():
    #         src_obj = self.boosting_manager.get_source_object(src)
    #         if src_obj and src_obj.ready:
    #             self._check_source_lc.stop()
    #             self._check_source_lc = None
    #             defer_param.callback(src)

    #     self._check_source_lc = LoopingCall(do_check)
    #     self._check_source_lc.start(1, now=True)

    #     return defer_param

# class TestBoostingManagerSysChannel(TestAsServer):
    # def __init__(self, *argv, **kwargs):
    #     super(TestBoostingManagerSysChannel, self).__init__(*argv, **kwargs)
    #     #     
    #     # 
    #     # self.expected_votecast_cid = None
    #     # self.expected_votecast_vote = None

    # @blocking_call_on_reactor_thread
    # @inlineCallbacks
    # def setUp(self, annotate=True, autoload_discovery=True):
    #     yield super(TestBoostingManagerSysChannel, self).setUp()
        # self.channel_db_handler = self.session.open_dbhandler(NTFY_CHANNELCAST)
        # self.channel_db_handler._get_my_dispersy_cid = lambda: "myfakedispersyid"

    # def set_boosting_settings(self):
    #     super(TestBoostingManagerSysChannel, self).set_boosting_settings()
    #     self.bsettings.swarm_interval = 1
    #     self.bsettings.initial_swarm_interval = 1
    #     self.bsettings.max_torrents_active = 1
    #     self.bsettings.max_torrents_per_source = 1

    # def _load(self, _):
    #     """
    #     Dummy method to download the torrent
    #     """
    #     return defer.succeed(self.tdef)

    # def create_torrents_in_channel(self, dispersy_cid_hex):
    #     """
    #     Helper function to insert 10 torrent into designated channel
    #     """
    #     for i in xrange(0, 10):
    #         self.insert_channel_in_db('rand%d' % i, 42 + i, 'Test channel %d' % i, 'Test description %d' % i)

    #     self.channel_id = self.insert_channel_in_db(dispersy_cid_hex.decode('hex'), 42,
    #                                                 'Simple Channel', 'Channel description')

    #     torrent_list = [[self.channel_id, 1, 1, TORRENT_UBUNTU_FILE_INFOHASH, 1460000000, TORRENT_UBUNTU_FILE,
    #                      self.tdef.get_files_with_length(), self.tdef.get_trackers_as_single_tuple()]]

    #     self.insert_torrents_into_channel(torrent_list)

    def test_get_source_object(self):
        '''
        test for getting a credit-mining source by source key
        '''
        self.boosting_manager.boosting_sources['fakesourcekey'] = self.mock_channel_source

        source = self.boosting_manager.get_source_object('fakesourcekey')
        self.assertEqual(source, self.mock_channel_source, "Can not get channel source by source key")

    def test_set_enable_mining(self):
        """
        test for dynamically enabling/disabling mining source
        #TODO not invoked anywhere
        """
        #create fake dispersy cid and torrent info hash
        dispersy_cid_hex = "abcd" * 9 + "0012"
        dispersy_cid = unhexlify(dispersy_cid_hex)
        info_hash = 'fakeinfohash'

        #create fake source and fake torrent.
        self.boosting_manager.boosting_sources[dispersy_cid] = MockChannelSource()
        self.boosting_manager.boosting_sources[dispersy_cid].enabled = False
        self.boosting_manager.torrents[info_hash] = {'enable': 'false', 'source': dispersy_cid_hex, "metainfo": MockTorrentDef(info_hash)}

        #mock _select_torrent()
        def fake_select_torrent():
            self._select_torrent = True
        self.boosting_manager._select_torrent = fake_select_torrent

        #mock stop_download()
        rm_torrent = []
        def fake_stop_download(infohash, remove_torrent=False, reason="N/A"):
            rm_torrent.append(infohash)
        self.boosting_manager.stop_download = fake_stop_download

        #start testing
        self.boosting_manager.set_enable_mining(dispersy_cid, True, True)
        self.assertTrue(self.boosting_manager.torrents[info_hash]['enable'], 'Boosting source can not be enabled.')
        self.assertEqual(len(rm_torrent), 0, 'Removing torrent unexpectedly.')
        self.assertTrue(self._select_torrent, 'Cannot force restart.')
        self.boosting_manager.set_enable_mining(dispersy_cid, False, False)
        self.assertEqual(len(rm_torrent), 1, 'Cannot remove torrent in disabled source.')

    def test_add_source(self):
        '''
        test for add source to boosting manager
        '''
        #create fake dispercy cid
        dispersy_cid_hex = "abcd" * 9 + "0012"
        dispersy_cid = unhexlify(dispersy_cid_hex)

        #mocking fake channel source
        self.boosting_manager.ChannelSource = MockChannelSource

        #start testing
        self.boosting_manager.add_source(dispersy_cid)
        self.boosting_manager.add_source('afakeandfailcid')
        self.boosting_manager.add_source(dispersy_cid)
        self.assertTrue(dispersy_cid in self.boosting_manager.boosting_sources.keys(), 'Cannot add source.')
        self.assertEqual(len(self.boosting_manager.boosting_sources), 1, 'Unexpected number of sources added.')

    def test_remove_source(self):
        '''
        test for removing source from boosting manager
        '''
        #create fake dispercy cid and torrent info hash
        dispersy_cid_hex = "abcd" * 9 + "0012"
        dispersy_cid = unhexlify(dispersy_cid_hex)
        info_hash1 = 'fakeinfohash1'
        info_hash2 = 'fakeinfohash2'

        #add fake boosting source
        self.boosting_manager.boosting_sources[dispersy_cid] = MockChannelSource()

        #add fake torrent
        self.boosting_manager.torrents[info_hash1] = {'source': dispersy_cid_hex, 'metainfo': MockTorrentDef(info_hash1)}
        self.boosting_manager.torrents[info_hash2] = {'source': dispersy_cid_hex[:29]+'0', 'metainfo': MockTorrentDef(info_hash1)}

        #mock stop_download()
        rm_torrent = []
        def fake_stop_download(infohash, remove_torrent=False, reason="N/A"):
            rm_torrent.append(infohash)
        self.boosting_manager.stop_download = fake_stop_download

        #start testing
        self.assertEqual(len(self.boosting_manager.boosting_sources), 1, 'Wrong number of boosting sources.')
        self.assertEqual(len(rm_torrent), 0, 'Removing torrent unexpectedly.')
        self.boosting_manager.remove_source(dispersy_cid)
        self.assertEqual(len(self.boosting_manager.boosting_sources), 0, 'Cannot remove source.')
        self.assertEqual(len(rm_torrent), 1, 'Cannot remove torrent in a removed source.')

    def test_insert_peer(self):
        '''
        test for storing a peer information to credit minging system
        '''
        #create fake infohash, ip and port
        infohash = 'fakeinfohash'
        ip = 'fakeip'
        port = 'fakeport'

        #insert fake torrent to boosting manager
        self.boosting_manager.torrents[infohash] = {'peers': {}}

        #start testing
        self.boosting_manager._BoostingManager__insert_peer(infohash, ip, port, {})
        self.assertEqual(self.boosting_manager.torrents[infohash]['peers']["%s:%s" % (ip, port)], {}, 'Cannot insert a new peer.')
        self.boosting_manager._BoostingManager__insert_peer(infohash, ip, port, {1:1})
        self.assertEqual(self.boosting_manager.torrents[infohash]['peers']["%s:%s" % (ip, port)], {1: 1}, 'Cannot insert an existing peer.')
        #TODO currently there is no difference between inserting a new peer or a existing peer.
    
    def test_process_resume_alert(self):
        pass

    def test_pre_download_torrent(self):
        pass

    def test_on_torrent_insert(self):
        '''
        test for inserting a torrent to boosting manager
        '''
        #create fake torrent and source
        info_hash1 = 'fakeinfohash1'
        info_hash2 = 'fakeinfohash2'
        source = 'fakechannelsource'
        torrent = {}
        torrent['name'] = 'faketorrentname'
        torrent['metainfo'] = MockTorrentDef(info_hash1)
        torrent['num_seeders'] = 10
        duplicate_torrent = torrent.copy()
        duplicate_torrent['num_seeders'] = 30
        duplicate_torrent['metainfo'] = MockTorrentDef(info_hash2)
        self.boosting_manager.boosting_sources[source] = MockChannelSource()

        #mock _pre_download_torrent
        self.boosting_manager._pre_download_torrent = lambda *_: Deferred()

        #start testing
        self.boosting_manager.on_torrent_insert(source, info_hash1, torrent)
        self.assertEqual(len(self.boosting_manager.torrents), 1, 'Torrent not correctly inserted.')
        self.assertEqual(self.boosting_manager.torrents[info_hash1]['name'], torrent['name'], 'Torrent not correctly inserted.')
        self.assertEqual(self.boosting_manager.torrents[info_hash1]['metainfo'], torrent['metainfo'], 'Torrent not correctly inserted.')
        self.assertEqual(self.boosting_manager.torrents[info_hash1]['num_seeders'], 10, 'Torrent not correctly inserted.')
        self.boosting_manager.on_torrent_insert(source, info_hash2, duplicate_torrent)
        self.assertEqual(len(self.boosting_manager.torrents), 2, 'Cannot insert duplicate torrent.')
        self.assertTrue(self.boosting_manager.torrents[info_hash1]['is_duplicate'], 'Duplicate torrent with fewer seeder is with wrong "is_duplicate" flag.')
        self.assertFalse(self.boosting_manager.torrents[info_hash2]['is_duplicate'], 'Duplicate torrent with more seeder is with wrong "is_duplicate" flag.')
      

    def test_on_torrent_notify(self):
        '''
        test for renewing the value in boosting manager when having new seeder/leecher value from tracker.
        '''
        #create fake torrents
        info_hash = 'fakeinfohash'
        info_hash_not_exist = 'notexistfakeinfohash'
        self.boosting_manager.torrents[info_hash] = {'metainfo': MockTorrentDef(info_hash), 'num_seeders': 10, 'num_leechers': 10}

        #mock getTorrent
        self.boosting_manager.torrent_db.getTorrent = lambda _, keys: {'infohash': info_hash, 'num_seeders': 30, 'num_leechers': 30}

        #start testing
        self.boosting_manager.on_torrent_notify(NTFY_TORRENTS, [NTFY_UPDATE], info_hash)
        self.boosting_manager.on_torrent_notify(NTFY_TORRENTS, [NTFY_UPDATE], info_hash_not_exist)
        self.assertEqual(len(self.boosting_manager.torrents), 1, 'Wrong number of torrents in boosting manager.')
        self.assertEqual(self.boosting_manager.torrents[info_hash]['num_seeders'], 30, 'Number of seeders is not updated in boosting manager.')
        self.assertEqual(self.boosting_manager.torrents[info_hash]['num_leechers'], 30, 'Number of leechers is not updated in boosting manager.')

    def test_scrape_trackers(self):
        pass
    
    def test_set_archive(self):
        '''
        test for seeting a boosting source to archive mode
        '''
        #create fake boosting source
        self.boosting_manager.boosting_sources['fakechannel'] = MockChannelSource()

        #start testing
        self.boosting_manager.set_archive('fakechannel', False)
        self.assertFalse(self.boosting_manager.boosting_sources['fakechannel'].archive, 'Cannot disable archive mode.')
        self.boosting_manager.set_archive('fakechannel', True)
        self.assertTrue(self.boosting_manager.boosting_sources['fakechannel'].archive, 'Cannot enable archive mode.')
        self.boosting_manager.set_archive('notexistfakechannel', False)
        self.assertEqual(len(self.boosting_manager.boosting_sources), 1, 'Cannot handle unknown sorce.')
        self.assertTrue(self.boosting_manager.boosting_sources['fakechannel'], 'Cannot handle unknown source.')

    def test_bdl_callback(self):
        '''
        test for __bdl_callback()
        '''
        #create fake torrents and DownloadState
        info_hash = 'fakeinfohash'
        download_state = MockDownloadState(info_hash)
        download_state_not_exist = MockDownloadState('notexistfakeinfohash')
        self.boosting_manager.torrents[info_hash] = {'metainfo': MockTorrentDef(info_hash), 'peers': {}}

        #start testing
        return_value = self.boosting_manager._BoostingManager__bdl_callback(download_state)
        return_value = self.boosting_manager._BoostingManager__bdl_callback(download_state_not_exist)
        self.assertEqual(return_value, (1.0, True), 'Wrong return value.')
        self.assertEqual(download_state.get_peerlist(), [{'ip': 'fakeip1', 'port': 'port1', 'have': [True, False]}, {'ip': 'fakeip2', 'port': 'port2', 'have': [True, True]}], 'Wrong peers selected.')
        self.assertEqual(self.boosting_manager.torrents[info_hash]['availability'], 30, 'Availability not updated.')
        self.assertEqual(self.boosting_manager.torrents[info_hash]['livepeers'], [{'ip': 'fakeip1', 'port': 'port1', 'have': [True, False]}, {'ip': 'fakeip2', 'port': 'port2', 'have': [True, True]}], 'Wrong live peers.')
        self.assertEqual(len(self.boosting_manager.torrents), 1, 'Wrong number of torrents in boosting manager.')

    def test_start_download(self):
        '''
        test for starting downloading a torrent and add it to the Tribler download list
        '''
        #create fake torrent
        info_hash1 = 'fakeinfohash1'
        info_hash2 = 'fakeinfohash2'
        info_hash_not_exist = 'notexistfakeinfohash'
        self.boosting_manager.torrents[info_hash1] = {'metainfo': MockTorrentDef(info_hash1), 'time': {}}
        self.boosting_manager.torrents[info_hash2] = {'metainfo': MockTorrentDef(info_hash2), 'time': {}}
        
        self.boosting_manager.torrents[info_hash1]['predownload'] = "_" + hexlify(info_hash1) + '.state'
        self.boosting_manager.torrents[info_hash2]['predownload'] = Deferred()

        #create fake predownloaded file
        filename = os.path.join(self.session.get_downloads_pstate_dir(), self.boosting_manager.torrents[info_hash1]['predownload'])
        open(filename, 'w')

        #mock the add() method to create fake MockLibtorrentDownloadImpl
        self.boosting_manager.session.lm.add = lambda tdef, dscfg, pstate, hidden, share_mode, checkpoint_disabled: MockLibtorrentDownloadImpl(info_hash1)

        #start testing
        self.boosting_manager.start_download(info_hash_not_exist)
        self.boosting_manager.start_download(info_hash1)
        self.boosting_manager.start_download(info_hash2)
        self.assertTrue('download' in self.boosting_manager.torrents[info_hash1], 'Download cannot be started.')
        self.assertTrue(isinstance(self.boosting_manager.torrents[info_hash1]['predownload'], str), 'Download cannot be started.')
        self.assertTrue('download' not in self.boosting_manager.torrents[info_hash2], 'Download starts before predownload finishes.')
        self.assertTrue(isinstance(self.boosting_manager.torrents[info_hash2]['predownload'], Deferred), 'Predownload callback has not been created.')
        self.assertEqual(len(self.boosting_manager.torrents), 2, 'Unknown torrent is added to boosting manager unexpectedly.')

    def test_stop_download(self):
        '''
        test for stopping a currently downloading torrent
        '''
        #create fake torrent
        info_hash1 = '111111'
        info_hash2 = '222222'
        info_hash3 = '333333'
        self.boosting_manager.torrents[info_hash1] = {'metainfo': MockTorrentDef(info_hash1), 'download': MockLibtorrentDownloadImpl(info_hash1), 'time': {}}
        self.boosting_manager.torrents[info_hash2] = {'metainfo': MockTorrentDef(info_hash2), 'download': MockLibtorrentDownloadImpl(info_hash2), 'time': {}}
        self.boosting_manager.torrents[info_hash3] = {'metainfo': MockTorrentDef(info_hash3), 'download': MockLibtorrentDownloadImpl(info_hash3), 'time': {}}

        self.boosting_manager.torrents[info_hash3]['download'].handle.is_valid = lambda: False
        self.boosting_manager.torrents[info_hash3]['download'].handle.has_metadata = lambda: False

        #create fake resume_data
        #TODO save_resume_data and on_save_resume_data_alert not reliable
        resume_data1 = {'info-hash': info_hash1}
        resume_data2 = {'info-hash': info_hash2}
        resume_data_not_exist = {'info-hash': 'notexistfakeinfohash'}

        #start testing
        self.boosting_manager.stop_download(info_hash1, remove_torrent=False)
        self.assertEqual(len(self.boosting_manager.torrents), 3, 'Torrent deleted before getting resume data unexpectedly.')
        self.boosting_manager.torrents[info_hash1]['download'].deferreds_resume[0].callback(resume_data1)
        self.assertEqual(len(self.boosting_manager.torrents), 3, 'Torrent deleted when stop downloading unexpectedly.')

        self.boosting_manager.stop_download(info_hash2, remove_torrent=True)
        self.assertEqual(len(self.boosting_manager.torrents), 3, 'Torrent deleted before getting resume data unexpectedly.')
        self.boosting_manager.torrents[info_hash2]['download'].deferreds_resume[0].callback(resume_data2)
        self.assertEqual(len(self.boosting_manager.torrents), 2, 'Torrent deleted when stop downloading unexpectedly.')

        self.boosting_manager.stop_download(info_hash3, remove_torrent=True)
        self.assertEqual(len(self.boosting_manager.torrents), 2, 'Torrent deleted when stop downloading unexpectedly.')
        self.boosting_manager.torrents[info_hash3]['download'].deferreds_resume[0].callback(resume_data_not_exist)
        self.assertEqual(len(self.boosting_manager.torrents), 2, 'Torrent deleted while info hash is unknown.')

    def test_select_torrent(self):
        '''
        test for selecting the torrents to be downloaded and stopped in the next iteration
        TODO not quite understand the policy here
        '''
        #create fake torrents
        self.boosting_manager.torrents['fakeinfohash1'] = {'metainfo': MockTorrentDef('fakeinfohash1')}
        self.boosting_manager.torrents['fakeinfohash2'] = {'metainfo': MockTorrentDef('fakeinfohash2')}
        self.boosting_manager.torrents['fakeinfohash3'] = {'metainfo': MockTorrentDef('fakeinfohash3')}

        self.boosting_manager.torrents['fakeinfohash1']['preload'] = True

        self.boosting_manager.torrents['fakeinfohash2']['preload'] = True
        self.boosting_manager.torrents['fakeinfohash2']['download'] = MockLibtorrentDownloadImpl('fakeinfohash2')
        self.boosting_manager.torrents['fakeinfohash2']['download'].get_status = lambda: DLSTATUS_SEEDING

        self.boosting_manager.torrents['fakeinfohash3']['is_duplicate'] = False

        #mock start_download()
        started_downloads = []
        def fake_start_download(info_hash):
            started_downloads.append(info_hash)
        self.boosting_manager.start_download = fake_start_download

        #mock stop_download()
        stopped_downloads = []
        def fake_stop_download(infohash, remove_torrent=False, reason="N/A"):
            stopped_downloads.append(infohash)
        self.boosting_manager.stop_download = fake_stop_download

        #mock boosting policy
        self.boosting_manager.settings.policy = MockBoostingPolicy()
        self.boosting_manager.settings.policy.apply = lambda *_: ([self.boosting_manager.torrents['fakeinfohash1']], [self.boosting_manager.torrents['fakeinfohash3']])

        #start test 
        self.boosting_manager._select_torrent()
        self.assertEqual(started_downloads, ['fakeinfohash1', 'fakeinfohash1'], 'Wrong torrents started downloading.')
        self.assertEqual(stopped_downloads, ['fakeinfohash2', 'fakeinfohash3'], 'Wrong torrents stopped downloading.')

    def test_load_config(self):
        '''
        test for loading file configuration and applying it to boosting manager
        '''
        #mock boosting policy
        self.session.get_cm_policy = lambda _: lambda _: MockBoostingPolicy()

        #mock session cm settings
        for k in SAVED_ATTR:
            setattr(self.session, "get_cm_%s" %k, lambda: 'fakesettingvalue')

        #mock boosting sources
        sources = {}
        sources['boosting_sources'] = ['fakechannel4', 'fakechannel1', 'fakechannel2', 'fakechannel3']
        sources['boosting_enabled'] = ['fakechannel1', 'fakechannel3']
        sources['boosting_disabled'] = ['fakechannel4', 'fakechannel2']
        sources['archive_sources'] = ['fakechannel1', 'fakechannel2']

        self.session.get_cm_sources = lambda: sources

        #mock add_source()
        added_sources = []
        def fake_add_source(source):
            added_sources.append(source)
        self.boosting_manager.add_source = fake_add_source

        #mock set_archive()
        archived_sources = []
        def fake_set_archive(source, enable):
            archived_sources.append(source + str(enable))
        self.boosting_manager.set_archive = fake_set_archive

        #mock enable_boosting()
        boosted_sources = []
        def fake_set_enable_mining(source, mining_bool=True, force_restart=False):
            boosted_sources.append(source + str(mining_bool))
        self.boosting_manager.set_enable_mining = fake_set_enable_mining

        #start testing
        self.boosting_manager.load_config()
        self.assertTrue(isinstance(self.boosting_manager.settings.policy, MockBoostingPolicy), 'Boosting policy not loaded.')
        for k in SAVED_ATTR:
            self.assertEqual(getattr(self.boosting_manager.settings, k), 'fakesettingvalue', 'Cannot load settings from session.')
        self.assertEqual(added_sources, ['fakechannel4', 'fakechannel1', 'fakechannel2', 'fakechannel3'], 'Incorrect sources added.')
        self.assertEqual(archived_sources, ['fakechannel1True', 'fakechannel2True'], 'Incorrect sources set to archive.')
        self.assertEqual(boosted_sources, ['fakechannel1True', 'fakechannel3True', 'fakechannel4False', 'fakechannel2False'], 'Incorrected sources enabled or disabled.')

    def test_save_config(self):
        '''
        test for saving environment parameters into configuration file
        '''
        #mock set configuration methods
        saved_config = []
        expected_config = []
        def fake_set_key(key):
            def fake_set_value(*value):
                string = ''
                for s in value:
                    string += str(s)
                saved_config.append(key + string)
            return fake_set_value
        for k in SAVED_ATTR:
            setattr(self.boosting_manager.session, "set_cm_%s" % k, fake_set_key(k))
            expected_config.append(k + str(getattr(self.bsettings, k)))

        #create fake sources
        self.boosting_manager.boosting_sources['fakechannel1'] = MockChannelSource()
        self.boosting_manager.boosting_sources['fakechannel2'] = MockChannelSource()
        self.boosting_manager.boosting_sources['fakechannel3'] = MockChannelSource()
        self.boosting_manager.boosting_sources['fakechannel4'] = MockChannelSource()

        self.boosting_manager.boosting_sources['fakechannel2'].enabled = False
        self.boosting_manager.boosting_sources['fakechannel3'].archive = False
        self.boosting_manager.boosting_sources['fakechannel4'].enabled = False
        self.boosting_manager.boosting_sources['fakechannel4'].archive = False

        expected_config.append("['fakechannel4', 'fakechannel1', 'fakechannel2', 'fakechannel3']boosting_sources")
        expected_config.append("['fakechannel1', 'fakechannel3']boosting_enabled")
        expected_config.append("['fakechannel4', 'fakechannel2']boosting_disabled")
        expected_config.append("['fakechannel1', 'fakechannel2']archive_sources")

        self.boosting_manager.session.set_cm_sources = fake_set_key('')

        #mock save_session_config
        save_to_disk = []
        def fake_save_session_config():
            save_to_disk.append(True)
        self.boosting_manager.session.save_session_config = fake_save_session_config

        #start testing
        self.boosting_manager.save_config()
        self.assertEqual(saved_config, expected_config, 'Cannot save configurations.')
        self.assertTrue(save_to_disk.pop(), 'Configuration is not saved to disk.')

        #test OperationNotPossibleAtRuntimeException
        saved_config = []
        expected_config = expected_config[1:]
        setattr(self.boosting_manager.session, "set_cm_%s" % SAVED_ATTR[0], lambda _: OperationNotPossibleAtRuntimeException())
        self.boosting_manager.save_config()
        self.assertEqual(saved_config, expected_config, 'Cannot handle OperationNotPossibleAtRuntimeException.')

    def test_log_statistics(self):
        '''
        test for log trasferring
        #TODO what is this used for? why need priorities here
        '''
        #mock get_torrents()
        info_hash = '123456'
        debug_msg = []
        def fake_get_torrents():
            return [MockLtTorrent(info_hash)]
        self.boosting_manager.session.lm.ltmgr.get_session().get_torrents = fake_get_torrents

        #mock logger functions
        def fake_logger(format_str, *arg):
            debug_msg.append((format_str % arg))
        self.boosting_manager._logger.debug = fake_logger
        self.boosting_manager.torrents = {unhexlify(info_hash): {}}

        #start testing
        self.boosting_manager.log_statistics()
        self.assertEqual(debug_msg, ['Status for 123456 : 0 0 | ul_lim : 12, max_ul 13, maxcon 14', 'Non zero priorities for 123456 : [1, 1, 1, 1, 1]'], 'Wrong log message.')
        debug_msg = []
        self.boosting_manager.session.lm.ltmgr.get_libtorrent_version = lambda: '1.0.9.0'
        self.boosting_manager.log_statistics()
        self.assertEqual(debug_msg, ['Status for 123456 : 0 0 | ul_lim : 12, max_ul 13, maxcon 14'], 'Wrong log message.')

    def test_check_time(self):
        '''
        test for checking the avtivity of a torrent
        '''
        #create fake torrents and libtorrent managers
        info_hash1 = 'fakeinfohash1'
        info_hash2 = 'fakeinfohash2'
        info_hash3 = 'fakeinfohash3'
        mock_ltmgr2 = MockLtSession()
        mock_ltmgr3 = MockLtSession()
        mock_ltmgr3.handle = None

        #insert fake torrents to boosting manager
        self.boosting_manager.torrents[info_hash1] = {}
        self.boosting_manager.torrents[info_hash2] = {'download': mock_ltmgr2, 'time': {'all_download': 5, 'all_upload': 5}}
        self.boosting_manager.torrents[info_hash3] = {'download': mock_ltmgr3, 'time': {'all_download': 5, 'all_upload': 5}}

        #start testing
        self.boosting_manager.check_time()
        self.assertEqual(self.boosting_manager.torrents[info_hash2]['time']['all_download'], 0, 'Cannot update download time.')
        self.assertEqual(self.boosting_manager.torrents[info_hash2]['time']['all_upload'], 0, 'Cannot update download time.')
        self.assertEqual(self.boosting_manager.torrents[info_hash3]['time']['all_download'], 5, 'Unexpected behaviour when no torrent handle available.')
        self.assertEqual(self.boosting_manager.torrents[info_hash3]['time']['all_upload'], 5, 'Unexpected behaviour when no torrent handle available.')

    def test_update_torrent_stats(self):
        '''
        test for updating swarm statistics
        '''
        #create fake torrent in boosting manager
        torrent_infohash_str = 'fakeinfohash'
        self.boosting_manager.torrents[torrent_infohash_str] = {'last_seeding_stats': {}}

        #start testing
        seeding_stats = {'time_seeding': 100}
        self.boosting_manager.update_torrent_stats(torrent_infohash_str, seeding_stats)
        self.assertEqual(self.boosting_manager.torrents[torrent_infohash_str]['last_seeding_stats']['time_seeding'], 100, 'Cannot create new torrent stats.')
        seeding_stats = {'time_seeding': 50}
        self.boosting_manager.update_torrent_stats(torrent_infohash_str, seeding_stats)
        self.assertEqual(self.boosting_manager.torrents[torrent_infohash_str]['last_seeding_stats']['time_seeding'], 100, 'Torrent stats updated unexpectedly.')
        seeding_stats = {'time_seeding': 200}
        self.boosting_manager.update_torrent_stats(torrent_infohash_str, seeding_stats)
        self.assertEqual(self.boosting_manager.torrents[torrent_infohash_str]['last_seeding_stats']['time_seeding'], 200, 'Cannot update torrent stats.')