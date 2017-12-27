"""
The class for the torrent object in Credit mining.
It get most data from LibtorrentDownloadImpl class and store them for further investigation.
Author: Bohao Zhang
"""
import time
import logging

class BoostingTorrent(object):
    def __init__(self, torrent_def):
        self._logger = logging.getLogger(self.__class__.__name__)
        self._logger.setLevel(1)
        # An instance of class Tribler.Core.TorrentDef, the defination of a torrent
        self.torrent_def = torrent_def

        self.num_files = len(torrent_def.get_files())

        # The LibtorrentDownloadImpl object
        self.download = None

        # Tribler.Core.DownloadState.DownloadState object
        self.last_downloadstate = None

        self.last_updatetime = -1

        # The set of BoostingSources which contains this torrent
        self.source = {}
        self.source_enabled = {}

        self.last_stopped = -1

        # The dictionary uses LibtorrentDownloadImpl.create_peerlist_data() result save value, and ip:port of the peer as key
        self.peer_dict = {}

        # seeding stats from DownloadState NEED EDIT
        self.last_seeding_stats = {}
    
    def update_downloadstate(self, download_state):
        self.last_downloadstate = download_state
        self.last_updatetime = time.time()
        return self

    def get_last_downloadstate(self):
        return self.last_downloadstate

    def get_infohash(self):
        """
        The function returns the infohash of this torrent in binary.
        """
        return self.torrent_def.get_infohash()

    def get_name(self):
        """
        The function returns the name of the torrent.
        """
        return self.torrent_def.get_name()

    def get_creation_date(self):
        """
        The function returns the creation date of the torrent
        """
        return self.torrent_def.get_creation_date()

    def get_seeder_num(self):
        """
        The function return the number of seeders
        """

    def get_length(self):
        """
        The function gets the file size of the torrent
        """
        return self.torrent_def.get_length()

    def get_total_upload(self):
        """
        The function gets the total upload from the torrent's LibtorrentDownloadImpl oject.
        If it is not created, return 0.
        """
        if self.download:
            return self.download.get_total_upload()
        else:
            return 0

    def get_download(self):
        """
        The function returns the LibtorrentDownloadImpl of the torrent
        """
        return self.download

    def set_enabled(self, enabled, source):
        """
        The function return set flag of the torrent being enabled for mining or not.
        Also add or remove the source from self.source_enabled if source is specified.

        @param source: The BoostingSource object from which this torrent is enabled or disabled.
        """
        if enabled:
            self.source_enabled[source.get_dispersy_cid()] = source
        else:
            del self.source_enabled[source.get_dispersy_cid()]
        return self

    def get_enabled(self):
        """
        The function return a bool whether this torrent is enabled to be mined.
        The result depends on whether self.source_enabled is empty or not.
        """
        return bool(self.source_enabled)

    def add_source(self, source):
        """
        Add a source in which this torrent appears.

        @param source: BoostingSource object
        """
        self.source[source.get_dispersy_cid()] = source
        return self

    def remove_source(self, source):
        """
        Remove a source from this torrent. Either the torrent is no longer in the source or the source no longer exists.

        @param source: BoostingSource object
        """
        del self.source[source.get_dispersy_cid]
        return self

    def get_source(self):
        """
        Return the dictory of sources in which this torrent appears.
        """
        return self.source

    def start_download(self, session, dscfg):
        """
        Add this torrent

        @param session: Tribler session
        @param dscfg: DownloadStartupConfig object
        """
        if self.download:
            self._logger.error("Task for torrent %s has already existed.", self.get_name())
            return

        # Not using session.start_download_from_tdef() because it does not allow disabling the checkpoint.
        self.download = session.lm.add(self.torrent_def, dscfg, hidden=True, checkpoint_disabled=True)

    def restart(self):
        """
        Resume the download of this torrent if it is pause.
        """
        if not self.download:
            self._logger.error("Task for torrent %s doesn't exist, thus cannot be restarted.", self.get_name())
            return

        self.download.restart()

    def stop(self):
        """
        Stop the download of this torrent.
        Also record the time to self.last_stopped.
        """
        if not self.download:
            self._logger.error("Task for torrent %s doesn't exist, thus cannot be stopped.", self.get_name())
            return
        self._logger.info("Stopping download %s", self.get_name())
        self.download.stop()
        #self.session.remove_download(self.download, hidden=True)
        self.last_stopped = time.time()
