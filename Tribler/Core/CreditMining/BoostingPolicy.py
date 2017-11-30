"""
Supported boosting policy.

Author(s): Egbert Bouman, Mihai Capota, Elric Milon, Ardhi Putra
"""
import logging
import random

import time
from binascii import hexlify

import operator

from Tribler.Core.simpledefs import DLSTATUS_DOWNLOADING, DLSTATUS_SEEDING, DLSTATUS_STOPPED


class BoostingPolicy(object):
    """
    Base class for determining what swarm selection policy will be applied
    """

    def __init__(self, session):
        self.session = session
        # function that checks if key can be applied to torrent
        self.reverse = None

        self._logger = logging.getLogger("BoostingPolicy")
        self._logger.setLevel(1)

    def apply(self, torrents, max_active):
        """
        apply the policy to the torrents stored
        """
        sorted_torrents = sorted([torrent for torrent in torrents.itervalues()
                                  if self.key_check(torrent)],
                                 key=self.key, reverse=self.reverse)

        torrents_start = []
        torrents_stop = []
        if max_active < len(sorted_torrents):
            for torrent in sorted_torrents[:max_active]:
                dl_impl = self.session.get_download(torrent["metainfo"].get_infohash())
                if not dl_impl or (dl_impl.get_status() == 5 or dl_impl.get_status() == 6):
                    torrents_start.append(torrent)
            for torrent in sorted_torrents[max_active:]:
                if self.session.get_download(torrent["metainfo"].get_infohash()):
                    torrents_stop.append(torrent)
        else:
            for torrent in sorted_torrents:
                if not self.session.get_download(torrent["metainfo"].get_infohash()):
                    torrents_start.append(torrent)
        return torrents_start, torrents_stop

    def key(self, key):
        """
        function to find a key of an object
        """
        return None

    def key_check(self, key):
        """
        function to check whether a swarm is included to download
        """
        return False


class RandomPolicy(BoostingPolicy):
    """
    A credit mining policy that chooses a swarm randomly
    """
    def __init__(self, session):
        BoostingPolicy.__init__(self, session)
        self.reverse = False

    def key_check(self, key):
        return True

    def key(self, key):
        return random.random()


class CreationDatePolicy(BoostingPolicy):
    """
    A credit mining policy that chooses swarm by its creation date

    The idea is, older swarms need to be boosted.
    """
    def __init__(self, session):
        BoostingPolicy.__init__(self, session)
        self.reverse = True

    def key_check(self, key):
        return key['creation_date'] > 0

    def key(self, key):
        return key['creation_date']


class SeederRatioPolicy(BoostingPolicy):
    """
    Default policy. Find the most underseeded swarm to boost.
    """
    def __init__(self, session):
        BoostingPolicy.__init__(self, session)
        self.reverse = False

    def key(self, key):
        return key['num_seeders'] / float(key['num_seeders'] + key['num_leechers'])

    def key_check(self, key):
        return (key['num_seeders'] + key['num_leechers']) > 0


class ScoringPolicy(SeederRatioPolicy):

    _MULTIPLIER = {
        "leechratio": 5,
        "peerratio": 3,
        "availability": 4
    }

    _SCORE = {
        "low_speed": 0.3,
        "high_speed": 0.5
    }

    def __init__(self, session):
        BoostingPolicy.__init__(self, session)

    def apply(self, torrents, max_active, force=False):
        # scoring mechanism :
        # - lower seeder get higher score
        # - higher number of peer get higher score
        # - lower availability get higher score
        # - if there was a downloading activity, give more score
        total_speed = {}
        total_active = {}
        avg_speed = {}
        scores = {}

        torrents_start = []
        torrents_stop = []

        total_peers = sum([len(t['peers']) for t in torrents.itervalues()])

        for ihash, t in torrents.iteritems():

            leech_ratio = 1.0 - (self.key(t) if self.key_check(t) else 1.0)
            peer_ratio = float(len(t['peers']))/(float(total_peers) or 1.0)
            avail_ratio = 1.0 - (float(t['availability'])/len(t['livepeers']) if len(t['livepeers']) else 1.0)

            self._logger.debug("%s l:%f p:%f a:%f", hexlify(ihash), self._MULTIPLIER['leechratio'] * leech_ratio,
                               self._MULTIPLIER['peerratio'] * peer_ratio,
                               self._MULTIPLIER['availability'] * avail_ratio)

            score = self._MULTIPLIER['leechratio'] * leech_ratio + self._MULTIPLIER['peerratio'] * peer_ratio + self._MULTIPLIER['availability'] * avail_ratio

            total_speed[ihash] = 0
            total_active[ihash] = 0
            for ip_port, peer in t['peers'].iteritems():
                if peer['speed'] != 0:
                    total_speed[ihash] += peer['speed']
                    total_active[ihash] += 1

            scores[ihash] = score

        for ihash, speed in total_speed.iteritems():
            avg_speed[ihash] = speed/total_active[ihash] if total_active[ihash] else 0

        sorted_tspeed = sorted(avg_speed.items(), key=operator.itemgetter(1))

        for ihash, _ in sorted_tspeed[:len(sorted_tspeed)/2]:
            scores[ihash] += self._SCORE['low_speed']
        for ihash, _ in sorted_tspeed[len(sorted_tspeed)/2:]:
            scores[ihash] += self._SCORE['high_speed']

        sorted_scores = sorted(scores.items(), key=operator.itemgetter(1), reverse=True)

        for ihash, score in sorted_scores[:max_active]:
            if not self.session.get_download(ihash):
                torrents_start.append(torrents[ihash])

        for ihash, score in sorted_scores[max_active:]:
            if self.session.get_download(ihash):
                torrents_stop.append(torrents[ihash])

        self._logger.debug("Max active : %d", max_active)
        for ihash, score in sorted_scores:
            self._logger.debug("Score %s : %f", hexlify(ihash), score)

        return torrents_start, torrents_stop


class JohanPolicy(BoostingPolicy):  # can be optimized. key_check is the worst for this policy
    def __init__(self, session):
        BoostingPolicy.__init__(self, session)
        self.reverse = False
        self.reserve_num = 5
        self.upload_last = {}  # total upload in torrent_status at the end of last interval.
        self.timestamp = 0

    def apply(self, torrents, max_active):
        torrents_start = []
        torrents_stop = []

        torrent_inactive = [torrent for torrent in torrents.itervalues() if not self.key_check(torrent)]

        num_start = max_active-len(torrents)+len(torrent_inactive)+self.reserve_num

        if num_start < 0:
            num_start = 0

        if num_start > len(torrent_inactive):
            num_start = len(torrent_inactive)

        torrents_start = random.sample(torrent_inactive, num_start)

        sorted_torrents = sorted([torrent for torrent in torrents.itervalues()
                                  if self.key_check(torrent)],
                                 key=self.key, reverse=self.reverse)

        # if max_active <= len(sorted_torrents):
        for torrent in sorted_torrents[max_active:]:
            infohash = torrent["metainfo"].get_infohash()
            if self.session.get_download(infohash):
                torrents_stop.append(torrent)

        for torrent in torrents.itervalues():
            infohash = torrent['metainfo'].get_infohash()
            torrent_impl = self.session.get_download(infohash)
            if torrent_impl:
                self.upload_last[infohash] = torrent_impl.get_total_upload()

        
        #########################################
        import csv
        import os
        torrent_start_log = [torrent['metainfo'].get_infohash() for torrent in torrents_start]
        torrent_infohash_log = [torrent['metainfo'].get_infohash() for torrent in sorted_torrents]
        torrent_stop_log = [torrent['metainfo'].get_infohash() for torrent in torrents_stop]
        torrent_new_upload_log = [self.key(torrent) for torrent in sorted_torrents]
        torrent_total_upload_log = [self.key(torrent) for torrent in sorted_torrents]

        fields = [self.timestamp, torrent_start_log, torrent_stop_log, torrent_infohash_log, torrent_new_upload_log, torrent_total_upload_log]
        with open(os.path.join(self.session.config.get_state_dir(), "test_log.csv"), 'a') as t:
            writer = csv.writer(t)
            writer.writerow(fields)

        self.timestamp += self.session.config.get_credit_mining_swarm_interval()

        #########################################

        return torrents_start, torrents_stop

    def key(self, key):
        infohash = key['metainfo'].get_infohash()
        torrent = self.session.get_download(infohash)
        if not torrent:
            return 0
        if infohash not in self.upload_last:
            return torrent.get_total_upload()
        return torrent.get_total_upload() - self.upload_last[infohash]

    def key_check(self, key):
        infohash = key['metainfo'].get_infohash()
        torrent = self.session.get_download(infohash)
        return torrent and not torrent.get_status() == DLSTATUS_STOPPED
