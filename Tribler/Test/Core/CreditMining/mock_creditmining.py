"""
Module of Credit mining mock classes.

Author(s): Ardhi Putra
"""
from twisted.web.resource import Resource
from twisted.internet.defer import Deferred

from Tribler.Core.CreditMining.BoostingManager import BoostingSettings
from Tribler.Core.CreditMining.BoostingPolicy import SeederRatioPolicy
from Tribler.Test.Core.base_test import MockObject


class MockLtTorrent(object):
    """
    Class representing libtorrent handle for getting peer info
    """
    def __init__(self, infohash="12345"):
        self.info_hash = infohash
        self.all_time_download = 0
        self.all_time_upload = 0

    def upload_limit(self):
        return 12

    def max_uploads(self):
        return 13

    def max_connections(self):
        return 14

    def piece_priorities(self):
        return [0, 1, 1, 0, 1, 1, 1]

    def get_peer_info(self):
        """
        class returning peer info for a particular handle
        """
        peer = [None] * 6
        peer[0] = MockLtPeer(MockPeerId("1"), "ip1")
        peer[0].setvalue(True, True, True)
        peer[1] = MockLtPeer(MockPeerId("2"), "ip2")
        peer[1].setvalue(False, False, True)
        peer[2] = MockLtPeer(MockPeerId("3"), "ip3")
        peer[2].setvalue(True, False, True)
        peer[3] = MockLtPeer(MockPeerId("4"), "ip4")
        peer[3].setvalue(False, True, False)
        peer[4] = MockLtPeer(MockPeerId("5"), "ip5")
        peer[4].setvalue(False, True, True)
        peer[5] = MockLtPeer(MockPeerId("6"), "ip6")
        peer[5].setvalue(False, False, False)
        return peer

    def is_valid(self):
        """
        check whether the handle is valid or not
        """
        return True

    def has_metadata(self):
        return True

    def status(self):
        return self

    def pause(self):
        pass

    def resume(self):
        pass


class MockLtPeer(object):
    """
    Dummy peer object returned by libtorrent python binding
    """
    def __init__(self, pid, ip):
        self.pid = pid
        self.client = 2
        self.ip = [ip, "port"]
        self.flags = 1
        self.local_connection = True
        self.payload_up_speed = 0
        self.remote_interested = 1
        self.remote_choked = 1
        self.upload_queue_length = 0
        self.used_send_buffer = 0
        self.payload_down_speed = 0
        self.interesting = True
        self.choked = True
        self.total_upload = 0
        self.total_download = 0
        self.progress = 0
        self.pieces = 0
        self.remote_dl_rate = 0
        self.country = "ID"
        self.connection_type = 0
        self.seed = 1
        self.upload_only = 1
        self.read_state = False
        self.write_state = False
        self.up_speed = 0
        self.down_speed = 0
        self.upload_rate_peak = 0
        self.download_rate_peak = 0
        self.progress_ppm = 0
        self.num_pieces = 1
        self.source = 1
        self.rtt = 0
        self.estimated_reciprocation_rate = 1

    def setvalue(self, upload_only, uinterested, completed):
        self.upload_only = upload_only
        self.remote_interested = uinterested
        self.progress = 1 if completed else 0
        self.progress_ppm = 1000000 if completed else 0


class MockPeerId(object):
    """
    This class is used to mock a peer id in libtorrent.
    """
    def __init__(self, peer_id):
        self.peer_id = peer_id

    def to_bytes(self):
        return self.peer_id


class MockLtSession(object):
    """
    Mock for session and LibTorrentMgr
    """
    def __init__(self):
        self.lm = MockObject()
        self.lm.ltmgr = MockObject()
        self.lm.boosting_manager = MockObject()
        self.lm.download_exists = MockObject()
        self.lm.channelcast_db = MockObject()
        self.lm.torrent_store = MockObject()
        self.lm.load_download_pstate_noexc = lambda _:MockObject()

        self.config = MockObject()
        self.config.get_credit_mining_policy = lambda as_class=False: SeederRatioPolicy if as_class else "seederratio"
        self.config.get_libtorrent_enabled = lambda: True
        self.config.get_megacache_enabled = lambda: False
        self.config.get_credit_mining_source_interval = lambda: 99
        self.config.get_credit_mining_archive_sources = lambda: []

        boosting_settings = BoostingSettings()
        self.config.get_credit_mining_max_torrents_per_source = lambda: boosting_settings.max_torrents_per_source
        self.config.get_credit_mining_max_torrents_active = lambda: boosting_settings.max_torrents_active
        self.config.get_credit_mining_source_interval = lambda: boosting_settings.source_interval
        self.config.get_credit_mining_swarm_interval = lambda: boosting_settings.swarm_interval
        self.config.get_credit_mining_share_mode_target = lambda: boosting_settings.share_mode_target
        self.config.get_credit_mining_tracker_interval = lambda: boosting_settings.tracker_interval
        self.config.get_credit_mining_logging_interval = lambda: boosting_settings.logging_interval
        self.config.get_credit_mining_sources = lambda: {}

        self.add_observer = lambda *_: None

        self.handle = MockLtTorrent()

    def get_libtorrent_version(self):
        return '0'

    def get_session(self):
        """
        supposed to get libtorrent session
        """
        return self

    def set_settings(self, _):
        """
        set settings (don't do anything)
        """
        pass

    def shutdown(self):
        """
        obligatory shutdown function
        """
        pass

    def get_download(self, x):
        """
        mocked function to get a download
        """
        return int(x) % 2

    def get_torrents(self):
        """
        mocked function to get torrents
        """
        return {}


class ResourceFailClass(Resource):
    def render_GET(self, request):
        request.setResponseCode(503)
        return "<html><body>Error 503.</body></html>"


class MockChannelSource(object):
    """
    Mock for channel source
    """
    def __init__(self):
        self.enabled = True
        self.archive = True

    def kill_tasks(self):
        pass

class MockBoostingPolicy(object):
    """
    Mock for boosting policy
    """
    def __init__(self):
        pass

    def apply(self, torrents, max_active):
        return [], []

class MockLibtorrentDownloadImpl(object):
    """
    Mock for LibtorrentDownloadImpl
    """
    def __init__(self, info_hash):
        self.handle = MockLtTorrent()
        self.deferreds_resume = []
        self.info_hash = info_hash

    def set_priority(self, prio):
        pass

    def get_status(self):
        return 0

    def get_def(self):
        return MockTorrentDef(self.info_hash)

    def save_resume_data(self):
        d = Deferred()
        self.deferreds_resume.append(d)
        return d

    def stop_remove(self, removestate, removecontent):
        pass

    def setup(self, dcfg=None, pstate=None, wrapperDelay=0, share_mode=False, checkpoint_disabled=False):
        return Deferred()

    def set_state_callback(self, usercallback, getpeerlist=False):
        pass

class MockTorrentDef(object):
    """
    Mock for torrentDef
    """
    def __init__(self, info_hash):
        self.info_hash = info_hash

    def get_infohash(self):
        return self.info_hash

    def is_finalized(self):
        return True

    def get_files_with_length(self):
        return []

    def get_metainfo(self):
        return {'info': {'piece length': 32, 'name': 'fakename', 'length': 66}}

class MockDownloadState(object):
    '''
    Mock for DownloadState
    '''
    def __init__(self, info_hash):
        self.stats = {}
        self.download = MockLibtorrentDownloadImpl(info_hash)

    def get_download(self):
        return self.download

    def get_peerlist(self):
        if 'spew' not in self.stats:
            self.stats['spew'] = []
            self.stats['spew'].append({'ip': '127.0.0.33', 'port': '6666', 'have': [True, False]})
            self.stats['spew'].append({'ip': 'fakeip1', 'port': 'port1', 'have': [True, False]})
            self.stats['spew'].append({'ip': 'fakeip2', 'port': 'port2', 'have': [True, True]})
            self.stats['spew'].append({'ip': 'fakeip3', 'port': 'port3', 'have': [False, False]})
        return self.stats['spew']

    def get_availability(self):
        return 30

class MockStatsAlert(object):
    def __init__(self, info_hash, category_value):
        self.resume_data = {'info-hash': info_hash}
        self.category_value = category_value
        self.handle = MockLtTorrent(info_hash)
    
    def category(self):
        return self.category_value