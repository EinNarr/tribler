import os
import tempfile
import shutil
import time
import libtorrent

from Tribler.Test.test_as_server import AbstractServer, TESTS_DATA_DIR

from Tribler.Core.Utilities.torrent_utils import create_torrent_file


class TestTorrent(AbstractServer):

    def setUp(self):
        super(TestTorrent, self).setUp()

        self._ubuntu_torrent_name = u"ubuntu-15.04-desktop-amd64.iso.torrent"
        self._origin_torrent_path = os.path.join(TESTS_DATA_DIR, self._ubuntu_torrent_name)

        # create a temporary dir
        self._temp_dir = tempfile.mkdtemp()
        self._test_torrent_path = os.path.join(self._temp_dir, self._ubuntu_torrent_name)
        # copy the test torrent into the dir
        shutil.copyfile(self._origin_torrent_path, self._test_torrent_path)

    def tearDown(self):
        super(TestTorrent, self).tearDown()

        # remove the temporary dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_create_torrent(self):
        """
        Tests the create_torrent_file() function.
        """

        def on_torrent_created(result):
            # start a libtorrent session to check if the file is correct
            lt_session = libtorrent.session()
            p = {'save_path': self._temp_dir,
                 'ti': libtorrent.torrent_info(result['torrent_file_path'])}
            handle = lt_session.add_torrent(p)

            # if handle.is_valid() returns false, the created torrent file is invalid
            self.assertTrue(handle.is_valid())

            # cleanup libtorrent session
            lt_session.remove_torrent(handle)
            del lt_session

        params = {'': ''}
        create_torrent_file([self._test_torrent_path], params, callback=on_torrent_created)
