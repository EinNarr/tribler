"""
Supported boosting policy.

Author(s): Bohao Zhang, based on the work of Egbert Bouman, Mihai Capota, Elric Milon, Ardhi Putra
"""
import logging
import random
from heapq import nlargest

class BoostingPolicy(object):
    """
    Base class for determining what swarm selection policy will be applied
    """

    def __init__(self, session, torrents_enabled, torrents_boosting, max_active):
        """
        @param session: the Tribler session.
        @param torrents_enabled: the dictionary of torrent which are currently enabled.
        @param torrents_boosting: the dictionary of torrent which are being boosted.
        @param max_active: the max number of torrents that is allowed to be boosted at the same time.
        """
        self.session = session

        self.torrents_enabled = torrents_enabled
        self.torrents_boosting = torrents_boosting

        self.torrents_inactive = []
        self.torrents_candidate = []

        self.max_active = max_active

        self.chosen_ones = []

        ###########################################################
        self._logger = logging.getLogger("BoostingPolicy")
        self._logger.setLevel(1)
        ###########################################################

    def set_max_active(self, max_active):
        self.max_active = max_active
        return self

    def set_torrent_pool(self, torrents_enabled, torrents_boosting):
        self.torrents_boosting = torrents_boosting
        self.torrents_enabled = torrents_enabled

    def apply(self):
        """
        Function to apply the police and generate a list of torrents to be started and stopped in the next iteration.
        """
        # The list of torrent to be boosted in the next iteration.
        self.torrents_inactive = [torrent for infohash, torrent in self.torrents_enabled.items() if not self.session.lm.download_exists(infohash)]
        self.torrents_candidate = self.torrents_inactive + self.torrents_boosting.values()

        self.chosen_ones = nlargest(self.max_active, self.torrents_candidate, key=self.policy_key)

        torrents_start = [torrent for torrent in self.chosen_ones if not self.session.lm.download_exists(torrent.get_infohash())]
        torrents_stop = [torrent for torrent in self.torrents_boosting.values() if torrent not in self.chosen_ones]

        return torrents_start, torrents_stop

    def policy_key(self, torrent):
        """
        Function to generate potential of a torrent.
        The larger value this returns, the better potential this torrent has.

        @param torrent: BoostingTorrent object
        """
        return None

    def policy_filter(self, torrent):
        """
        Fuction to filter the torrent which are eligible for boosting.
        Return true by default.

        @param torrent: BoostingTorrent object
        """
        return True

class RandomPolicy(BoostingPolicy):
    """
    The policy that selects torrents randomly.
    """
    def policy_key(self, torrent):
        return random.random()

class CreationDatePolicy(BoostingPolicy):
    """
    The policy that selects the oldest swarms.
    """
    def policy_key(self, torrent):
        return -torrent.get_creation_date()

class SeederRatioPolicy(BoostingPolicy):
    """
    Find the swarm with lowest seeder/peers ratio to boost
    """
    def policy_key(self, torrent):
        seeders, peers = torrent.get_last_downloadstate().get_num_seeds_peers()
        return seeders/peers

class VitalityPolicy(BoostingPolicy):
    """
    The straightforward policy. Simply drop the a certain amount of torrents with worst vitality(upload performance).

    Named after the Vitality Curve policy applied by former General Electric chairman and CEO Jack Welch.
    """
    def __init__(self, session, torrents_enabled, torrents_boosting, max_active, reserved=3, threshold=1000):
        super(VitalityPolicy, self).__init__(session, torrents_enabled, torrents_boosting, max_active)

        # The dictionary of the total upload amount of torrents. Key:infohash, value: total upload at last investigation
        self.total_upload_record = {}

        self.reserved = reserved
        # If the torrent do not meet the threshold, directly drop it.
        self.threshold = threshold

        ############################
        self.timestamp = 0
        self.torrent_times = {}
        self.torrent_times_total = {}
        ############################

    def policy_key(self, torrent):
        infohash = torrent.get_infohash()
        # LibtorrentDownloadImpl object
        download = torrent.get_download()

        total_upload = download.get_total_upload() if download else 0
        total_upload_last = self.total_upload_record[infohash] if infohash in self.total_upload_record else 0
        if not total_upload:
            total_upload = 0
            total_upload_last = 0
            
        upload = total_upload-total_upload_last

        # update the latest data to the record dictionary
        if download:
            self.total_upload_record[infohash] = total_upload
            # dead swarm detection: if the total upload amount is lower than a certain threshold
            # the swarm is determined to be dead, and is given a -1 key (normally the key could only be as low as 0)
            # thus the torrent would be put in the very bottom of the queue.
            if upload < self.threshold:
                return -1

        return upload

    def apply(self):
        torrents_start, torrents_stop = super(VitalityPolicy, self).apply()

        # the total number of torrents to be boosted in the next interation is max_active+reserved
        num_to_start = (self.max_active + self.reserved) - (len(self.torrents_boosting) + len(torrents_start) -len(torrents_stop))
        # if num_to_start < 0:
        if num_to_start > len(self.torrents_inactive):
            num_to_start = len(self.torrents_inactive)

        rand = random.sample(self.torrents_inactive, num_to_start)

        torrents_start = torrents_start + rand

        #########################################
        import csv
        import os
        from binascii import hexlify
        torrent_start_log = [hexlify(torrent.get_infohash()) for torrent in torrents_start]
        torrent_infohash_log = [hexlify(torrent.get_infohash()) for torrent in self.chosen_ones]
        torrent_stop_log = [hexlify(torrent.get_infohash()) for torrent in torrents_stop]
        torrent_new_upload_log = [torrent.get_total_upload() for torrent in self.chosen_ones]
        torrent_total_upload_log = [self.total_upload_record[torrent.get_infohash()] if torrent.get_infohash() in self.total_upload_record else 0 for torrent in self.chosen_ones]

        import time

        for torrent in self.chosen_ones:
            if hexlify(torrent.get_infohash()) in self.torrent_times:
                self.torrent_times[hexlify(torrent.get_infohash())] += 1
            else:
                self.torrent_times[hexlify(torrent.get_infohash())] = 1
            if hexlify(torrent.get_infohash()) in self.torrent_times_total:
                self.torrent_times_total[hexlify(torrent.get_infohash())] += 1
            else:
                self.torrent_times_total[hexlify(torrent.get_infohash())] = 1

        for torrent in rand:
            if hexlify(torrent.get_infohash()) in self.torrent_times_total:
                self.torrent_times_total[hexlify(torrent.get_infohash())] += 1
            else:
                self.torrent_times_total[hexlify(torrent.get_infohash())] = 1

        fields = [self.timestamp, time.time(), torrent_start_log, torrent_stop_log, torrent_infohash_log, torrent_new_upload_log, torrent_total_upload_log, len(self.torrents_boosting), self.torrent_times, self.torrent_times_total]
        with open(os.path.join(self.session.config.get_state_dir(), "test_log.csv"), 'a') as t:
            writer = csv.writer(t)
            writer.writerow(fields)
        
        fields = []
        with open(os.path.join(self.session.config.get_state_dir(), "torrent_log.csv"), 'a') as t:
            writer = csv.writer(t)
            writer.writerow(fields)

        self.timestamp += self.session.config.get_credit_mining_swarm_interval()

        #########################################

        return torrents_start, torrents_stop
