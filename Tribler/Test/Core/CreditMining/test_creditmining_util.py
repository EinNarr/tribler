"""
Module of Credit mining unility function testing.

Author(s): Ardhi Putra, Bohao Zhang
"""

from binascii import hexlify, unhexlify

import os
import re

from twisted.internet.defer import inlineCallbacks

import Tribler.Core.CreditMining.BoostingManager as bm

from Tribler.Core.CreditMining.credit_mining_util import ent2chr, validate_source_string, compare_torrents
from Tribler.Core.CreditMining.credit_mining_util import levenshtein_dist, source_to_string, string_to_source
from Tribler.Core.DownloadConfig import DefaultDownloadStartupConfig
from Tribler.Core.TorrentDef import TorrentDef
from Tribler.Test.common import TESTS_DATA_DIR, TORRENT_UBUNTU_FILE
from Tribler.Test.Core.CreditMining.mock_creditmining import MockLtPeer, MockLtSession, MockPeerId
from Tribler.Test.Core.base_test import TriblerCoreTest
from Tribler.dispersy.util import blocking_call_on_reactor_thread


class TestBoostingManagerUtilities(TriblerCoreTest):
    """
    Test several utilities used in credit mining
    """

    def __init__(self, *argv, **kwargs):
        super(TestBoostingManagerUtilities, self).__init__(*argv, **kwargs)

        self.peer = [None] * 6
        self.peer[0] = MockLtPeer(MockPeerId("1"), "ip1")
        self.peer[0].setvalue(True, True, True)
        self.peer[1] = MockLtPeer(MockPeerId("2"), "ip2")
        self.peer[1].setvalue(False, False, True)
        self.peer[2] = MockLtPeer(MockPeerId("3"), "ip3")
        self.peer[2].setvalue(True, False, True)
        self.peer[3] = MockLtPeer(MockPeerId("4"), "ip4")
        self.peer[3].setvalue(False, True, False)
        self.peer[4] = MockLtPeer(MockPeerId("5"), "ip5")
        self.peer[4].setvalue(False, True, True)
        self.peer[5] = MockLtPeer(MockPeerId("6"), "ip6")
        self.peer[5].setvalue(False, False, False)

        self.session = MockLtSession()

    @blocking_call_on_reactor_thread
    @inlineCallbacks
    def setUp(self, autoload_discovery=True):
        yield super(TestBoostingManagerUtilities, self).setUp()

        self.session.get_libtorrent = lambda: True

        self.bsettings = bm.BoostingSettings(self.session)
        self.bsettings.credit_mining_path = self.session_base_dir
        self.bsettings.load_config = False
        self.bsettings.check_dependencies = False
        self.bsettings.initial_logging_interval = 900

    def tearDown(self, annotate=True):
        # TODO(ardhi) : remove it when Tribler free of singleton
        # and 1 below
        DefaultDownloadStartupConfig.delInstance()

        super(TestBoostingManagerUtilities, self).tearDown()

    def test_validate_source_string(self):
        """
        test to check whether a source string is a valid source or not
        """
        self.assertEqual(unhexlify("0" * 40), validate_source_string("0" * 40),
                         'Hexlified string not recognized as source')
        self.assertEqual(("0" * 39), validate_source_string("0" * 39), 'String with wrong length recognized as source')
        self.assertEqual(("http" + "0" * 36), validate_source_string("http" + "0" * 36),
                         'Http address recognized as source')

    def test_levenshtein(self):
        """
        test levenshtein between two string (in this case, file name)

        source :
        http://people.cs.pitt.edu/~kirk/cs1501/Pruhs/Fall2006/Assignments/editdistance/Levenshtein%20Distance.htm
        """
        string1 = "GUMBO"
        string2 = "GAMBOL"
        dist = levenshtein_dist(string1, string2)
        dist_swap = levenshtein_dist(string2, string1)

        # random string check
        self.assertEqual(dist, 2, "Wrong levenshtein distance")
        self.assertEqual(dist_swap, 2, "Wrong levenshtein distance")

        string1 = "ubuntu-15.10-desktop-i386.iso"
        string2 = "ubuntu-15.10-desktop-amd64.iso"
        dist = levenshtein_dist(string1, string2)

        # similar filename check
        self.assertEqual(dist, 4, "Wrong levenshtein distance")

        dist = levenshtein_dist(string1, string1)
        # equal filename check
        self.assertEqual(dist, 0, "Wrong levenshtein distance")

        string2 = "Learning-Ubuntu-Linux-Server.tgz"
        dist = levenshtein_dist(string1, string2)
        # equal filename check
        self.assertEqual(dist, 28, "Wrong levenshtein distance")

    def test_source_to_string(self):
        """
        test converting source to string
        """
        self.assertEqual(hexlify("0" * 20), source_to_string("0" * 20), 'Unhexlified string not converted')
        self.assertEqual(("0" * 19), source_to_string("0" * 19), 'String with wrong length converted')
        self.assertEqual(("http://" + "0" * 13), source_to_string("http://" + "0" * 13), "Http address converted")
        self.assertEqual(("https://" + "0" * 12), source_to_string("https://" + "0" * 12), "Https address converted")

    def test_string_to_source(self):
        """
        test converting string to source
        """
        self.assertEqual(unhexlify("0" * 40), string_to_source("0" * 40), 'Hexlified string not converted')
        self.assertEqual(("0" * 39), string_to_source("0" * 39), 'String with wrong length converted')
        self.assertEqual(("http://" + "0" * 33), string_to_source("http://" + "0" * 33), "Http address converted")
        self.assertEqual(("https://" + "0" * 32), string_to_source("https://" + "0" * 32), "Https address converted")

    def test_compare_torrents(self):
        """
        test comparing torrents
        """
        torrent_names = []
        torrent_names.append(os.path.join(TESTS_DATA_DIR, "Night.Of.The.Living.Dead_1080p_archive.torrent"))
        torrent_names.append(os.path.join(TESTS_DATA_DIR,
                                          "Night.Of.The.Living.Dead_1080p_archive_comparetest1.torrent"))
        torrent_names.append(os.path.join(TESTS_DATA_DIR, TORRENT_UBUNTU_FILE))
        torrent_names.append(os.path.join(TESTS_DATA_DIR,
                                          "Night.Of.The.Living.Dead_1080p_archive_comparetest2.torrent"))

        def get_dict(torrentDef):
            torrent = dict()
            torrent['name'] = torrentDef.get_name()
            torrent['metainfo'] = torrentDef
            torrent['creation_date'] = torrentDef.get_creation_date()
            torrent['length'] = torrentDef.get_length()
            torrent['num_files'] = len(torrentDef.get_files())
            return torrent

        torrents = []
        for name in torrent_names:
            torrents.append(get_dict(TorrentDef.load(name)))

        self.assertNotEqual(torrents[0]['metainfo'].get_infohash(), torrents[1]['metainfo'].get_infohash(),
                            "compared torrents have same infohash")

        self.assertTrue(compare_torrents(torrents[0], torrents[1]),
                        "Same swarm with different infohash undetected")
        self.assertFalse(compare_torrents(torrents[0], torrents[2]),
                         "Torrents with different number of files regarded as the same")
        self.assertFalse(compare_torrents(torrents[0], torrents[3]),
                         "Torrents with different size of file regarded as the same")

    def test_ent2chr(self):
        """
        testing escape symbols occured in xml/rss document file.
        """
        re_symbols = re.compile(r'\&\#(x?[0-9a-fA-F]+);')

        ampersand_str = re_symbols.sub(ent2chr, '&#x26;')
        self.assertEqual(ampersand_str, "&", "wrong ampersand conversion %s" % ampersand_str)

        str_123 = re_symbols.sub(ent2chr, "&#x31;&#x32;&#x33;")
        self.assertEqual(str_123, "123", "wrong number conversion %s" % str_123)
