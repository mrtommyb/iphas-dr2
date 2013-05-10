#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Identifies duplicate detections in the IPHAS catalogue.

This script will identify all the detections for a single astrophysical source
amongst the IPHAS observations using stilts crossmatching.
Using this information. the script will decide on the 'primary' (best)
detection and the 'partner' (same night) detection.
Finally, this information is then added to the bandmerged catalogues.

Computing requirements: the densest strips need ~4h CPU and ~7 GB RAM.

"""
from __future__ import division, print_function, unicode_literals
import os
import sys
import time
import numpy as np
import datetime
from multiprocessing import Pool
from astropy.io import fits
from astropy import log
import util
import constants
from constants import IPHASQC

__author__ = 'Geert Barentsen'
__copyright__ = 'Copyright, The Authors'
__credits__ = ['Hywel Farnhill', 'Robert Greimel', 'Janet Drew']


#############################
# CONSTANTS & CONFIGURATION
#############################

# Where to write the output catalogues?
MYDESTINATION = os.path.join(constants.DESTINATION, 'seamed')

# Where to store temporary files
TMPDIR = '/dev/shm'

# CACHE registers sourceID's for wich a primaryID has already been assigned
CACHE = {}  # (Beats any key-value db)


###########
# CLASSES
###########

class SeamingException(Exception):
    """Exception raised for unexpected problems with a single field."""
    pass


class SeamMachine(object):
    """Adds the primaryID column to a field's bandmerged catalogue.

    Usage:
       SeamMachine.run()
    """

    def __init__(self, fieldid, ra, dec, strip):
        """Constructor.

        fieldid -- e.g. 0001_aug2004
        ra -- field R.A. (degrees)
        dec -- field declination (degrees)
        strip -- longitude strip being seamed.
        """
        self.fieldid = fieldid
        self.ra = ra
        self.dec = dec
        self.strip = strip
        # Where to store temporary files?
        self.crossmatch_file = os.path.join(TMPDIR,
                                            'seaming_{0}_{1}.fits'.format(
                                                                    strip,
                                                                    fieldid))
        self.primaryid_file = os.path.join(TMPDIR,
                                           'seamids_{0}_{1}.fits'.format(
                                                                   strip,
                                                                   fieldid))
        # Where to store the results?
        self.output_dir = os.path.join(MYDESTINATION, 'strip{0}'.format(strip))
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.output_file = os.path.join(self.output_dir,
                                        '{0}.fits'.format(fieldid))

    def run(self):
        """Main function."""
        self.overlaps = self.overlaps()
        self.crossmatch()
        sourceID, matchinfo = self.get_primaryID()
        assert(len(sourceID) == len(matchinfo['primaryID']))  # sanity check
        assert(len(sourceID) == len(matchinfo['partnerID']))
        self.save(sourceID, matchinfo)
        self.clean()

    def clean(self):
        """Removes temporary files created by run()."""
        os.remove(self.crossmatch_file)
        os.remove(self.primaryid_file)

    def log_debug(self, message):
        log.debug('{0}: strip{1}: {2}'.format(
                                            str(datetime.datetime.now())[0:19],
                                            self.strip,
                                            message))

    def log_info(self, message):
        log.info('{0}: strip{1}: {2}'.format(
                                            str(datetime.datetime.now())[0:19],
                                            self.strip,
                                            message))

    def log_warning(self, message):
        log.warning('{0}: strip{1}: {2}'.format(
                                            str(datetime.datetime.now())[0:19],
                                            self.strip,
                                            message))

    def save(self, sourceID, matchinfo):
        """Writes a new catalogue with primaryID to disk."""
        # Write the (sourceID,primaryID)s to a table
        col_sourceID = fits.Column(name='sourceID', format='K', array=sourceID)
        col_nObs = fits.Column(name='nObs', format='B', 
                               array=matchinfo['nObs'])
        col_primaryID = fits.Column(name='primaryID', format='K',
                                    array=matchinfo['primaryID'])
        col_partnerID = fits.Column(name='partnerID', format='K',
                                    null=-9223372036854775808,
                                    array=matchinfo['partnerID'])
        col_r2 = fits.Column(name='r2', format='E', unit='Magnitude',
                             array=matchinfo['r2'])
        col_rErr2 = fits.Column(name='rErr2', format='E', unit='Sigma',
                                array=matchinfo['rErr2'])
        col_i2 = fits.Column(name='i2', format='E', unit='Magnitude',
                             array=matchinfo['i2'])
        col_iErr2 = fits.Column(name='iErr2', format='E', unit='Sigma',
                                array=matchinfo['iErr2'])
        col_ha2 = fits.Column(name='ha2', format='E', unit='Magnitude',
                              array=matchinfo['ha2'])
        col_haErr2 = fits.Column(name='haErr2', format='E', unit='Sigma',
                                 array=matchinfo['haErr2'])
        col_errBits2 = fits.Column(name='errBits2', format='J',
                                   null=-2147483648, unit='bitmask',
                                   array=matchinfo['errBits2'])
        cols = fits.ColDefs([col_sourceID,
                             col_nObs,
                             col_primaryID,
                             col_partnerID,
                             col_r2, col_rErr2,
                             col_i2, col_iErr2,
                             col_ha2, col_haErr2,
                             col_errBits2])
        newtable = fits.new_table(cols)
        newtable.writeto(self.primaryid_file, clobber=True)

        # Then use stilts to add the extra column
        config = {'STILTS': constants.STILTS,
                  'IN1': self.filename(self.fieldid),
                  'IN2': self.primaryid_file,
                  'OUT': self.output_file}

        cmd = "{STILTS} tmatch2 progress=none find=best1 in1={IN1} in2={IN2} "
        cmd += "matcher=exact join=all1 suffix1='' "
        cmd += "values1='sourceID' values2='sourceID' "
        cmd += "ocmd='delcols sourceID_2' out='{OUT}' "

        stilts_cmd = cmd.format(**config)
        log.debug(stilts_cmd)
        status = os.system(stilts_cmd)
        if status == 0:
            self.log_info('adding primaryID column: stilts returned '+str(status))
        else:
            self.log_warning('adding primaryID column: stilts returned '+str(status))
        return status

    def overlaps(self):
        """Returns the list of fields which overlap."""
        dist = util.sphere_dist(self.ra, self.dec, IPHASQC['ra'], IPHASQC['dec'])
        idx = (constants.IPHASQC_COND_RELEASE
               & (dist < constants.FIELD_MAXDIST)
               & (self.fieldid != IPHASQC['id']))
        return IPHASQC['id'][idx]

    def filename(self, fieldid):
        """Returns the path of the bandmerged catalogue for a given field."""
        return os.path.join(constants.DESTINATION,
                            'bandmerged',
                            '{0}.fits'.format(fieldid))

    def crossmatch_command(self):
        """Return the stilts command to crossmatch overlapping fields."""
        # Operations to perform on all tables
        icmd = """keepcols "sourceID fieldID ra dec nBands errBits seeing \
                             rAxis rMJD r rErr i iErr ha haErr" """
        # Keywords in stilts command
        config = {'STILTS': constants.STILTS,
                  'MATCHING_DISTANCE': constants.MATCHING_DISTANCE,
                  'NIN': len(self.overlaps) + 1,
                  'IN1': self.filename(self.fieldid),
                  'ICMD': icmd,
                  'OUT': self.crossmatch_file}
        # Create stilts command
        # FIXME: add progress=none
        cmd = "{STILTS} tmatchn progress=none "
        cmd += "matcher=sky params={MATCHING_DISTANCE} "
        cmd += "multimode=pairs iref=1 nin={NIN} "
        cmd += "in1={IN1} values1='ra dec' join1=always "
        cmd += "icmd1='{ICMD}' "
        for i in range(len(self.overlaps)):
            cmd += "in{0}={1} ".format(i+2,
                                       self.filename(self.overlaps[i]))
            cmd += "values{0}='ra dec' ".format(i+2)
            cmd += "icmd{0}='{ICMD}' ".format(i+2, **config)
        cmd += "out='{OUT}' "

        stilts_cmd = cmd.format(**config)
        return stilts_cmd

    def crossmatch(self):
        """Carry out the crossmatching of overlapping fields."""
        cmd = self.crossmatch_command()
        self.log_debug('Overlaps: {0}'.format(self.overlaps))
        self.log_debug(cmd)
        status = os.system(cmd)
        self.log_info('crossmatch: stilts returned '+str(status))
        if status == 256:
            raise SeamingException('Crossmatch failed for %s' % self.fieldid)
        return status

    def cache_get(self, sourceID):
        return CACHE[self.strip][sourceID]

    def cache_set(self, sourceID, primaryID):
        CACHE[self.strip][sourceID] = primaryID

    def get_primaryID(self):
        """Returns the tuple (sourceID, primaryID, partnerinfo).

        The ranking criteria are
          1. prefer filter coverage (3 > 2 > 1);
          2. prefer least severe error flags;
          3. prefer best seeing amongst the filters
             (but tolerate up to 20% of the best value);
          4. prefer smallest distance from optical axis.

        The function is optimised for speed at the expense of readability.
        """

        # Open the file with the sources crossmatched across fields
        try:
            crossmatch = fits.getdata(self.crossmatch_file, 1, Memmap=True)
        except OSError, e:  # Anticipate a "No such file" error
            log.warning('Failed to open {0}: {1}'.format(self.crossmatch_file,
                                                         e))
            log.warning('Will try again in 5 seconds.')
            time.sleep(5)
            crossmatch = fits.getdata(self.crossmatch_file, 1)

        # Which columns are important?
        idx = (np.arange(self.overlaps.size+1) + 1)  # 1 2 3 ...
        sourceID_cols = np.array(['sourceID_{0}'.format(i) for i in idx])
        nBands_cols = np.array(['nBands_{0}'.format(i) for i in idx])
        errBits_cols = np.array(['errBits_{0}'.format(i) for i in idx])
        seeing_cols = np.array(['seeing_{0}'.format(i) for i in idx])
        rAxis_cols = np.array(['rAxis_{0}'.format(i) for i in idx])
        rMJD_cols = np.array(['rMJD_{0}'.format(i) for i in idx])
        r_cols = np.array(['r_{0}'.format(i) for i in idx])
        rErr_cols = np.array(['rErr_{0}'.format(i) for i in idx])
        i_cols = np.array(['i_{0}'.format(i) for i in idx])
        iErr_cols = np.array(['iErr_{0}'.format(i) for i in idx])
        ha_cols = np.array(['ha_{0}'.format(i) for i in idx])
        haErr_cols = np.array(['haErr_{0}'.format(i) for i in idx])

        # Save in memory to speed up what follows
        matchdata = {}
        for col in np.concatenate((sourceID_cols, nBands_cols, errBits_cols,
                                   seeing_cols, rAxis_cols, rMJD_cols,
                                   r_cols, rErr_cols,
                                   i_cols, iErr_cols,
                                   ha_cols, haErr_cols)):
            matchdata[col] = crossmatch[col]

        # This array keeps track of which candidates are still in the running
        winning_template = np.array([True for i in idx])

        primaryID = []  # will hold the result
        partnerID = []
        partner_r, partner_rErr = [], []
        partner_i, partner_iErr = [], []
        partner_ha, partner_haErr = [], []
        partner_errBits = []
        nObs = []

        for rowno in range(matchdata['sourceID_1'].size):
            sourceID = np.array([matchdata[col][rowno]
                                 for col in sourceID_cols])
            mySourceID = sourceID[0]  # sourceID of the current row

            # Count number of observations
            n = (sourceID > 0).sum()
            nObs.append(n)

            # First, identify the partnerID
            mjd = np.array([matchdata[col][rowno] for col in rMJD_cols])
            cond_partner = np.abs(mjd[1:] - mjd[0]) < 0.5  # within 30 mins
            if cond_partner.sum() > 0:  # Partner found!
                # Index 0 is the source itself!
                idx_partner = 1 + cond_partner.nonzero()[0][0]
                partnerID.append(sourceID[idx_partner])

                partner_r.append(
                    matchdata[r_cols[idx_partner]][rowno])
                partner_rErr.append(
                    matchdata[rErr_cols[idx_partner]][rowno])
                partner_i.append(
                    matchdata[i_cols[idx_partner]][rowno])
                partner_iErr.append(
                    matchdata[iErr_cols[idx_partner]][rowno])
                partner_ha.append(
                    matchdata[ha_cols[idx_partner]][rowno])
                partner_haErr.append(
                    matchdata[haErr_cols[idx_partner]][rowno])
                partner_errBits.append(
                    matchdata[errBits_cols[idx_partner]][rowno])
            else:
                partnerID.append(-9223372036854775808)  # null value
                partner_r.append(np.nan)
                partner_rErr.append(np.nan)
                partner_i.append(np.nan)
                partner_iErr.append(np.nan)
                partner_ha.append(np.nan)
                partner_haErr.append(np.nan)
                partner_errBits.append(-2147483648)  # null value

            # If we have encountered the source before, we enforce the
            # previous conclusion to ensure consistency
            if mySourceID in CACHE[self.strip]:
                winnerID = self.cache_get(mySourceID)
                del CACHE[self.strip][mySourceID]  # Save memory
            else:

                # Discard unmatched sources
                win = winning_template.copy()
                win[sourceID < 0] = False

                if win.sum() > 1:
                    # Discard source with few bands
                    nBands = np.array([matchdata[col][rowno]
                                      for col in nBands_cols[win]])
                    win[win.nonzero()[0][nBands < nBands.max()]] = False

                    # Discard sources with large errBits
                    errbits = np.array([matchdata[col][rowno]
                                        for col in errBits_cols[win]])
                    win[win.nonzero()[0][errbits > errbits.min()]] = False

                    # Discard sources with poor seeing
                    seeing = np.array([matchdata[col][rowno]
                                       for col in seeing_cols[win]])
                    win[win.nonzero()[0][seeing > 1.2 * seeing.min()]] = False

                    if win.sum() > 1:
                        # If there are still candidates left at this point
                        # then the one closest to the optical axis wins
                        raxis = np.array([matchdata[col][rowno]
                                          for col in rAxis_cols[win]])
                        win[win.nonzero()[0][raxis > raxis.min()]] = False

                # This shouldn't happen
                if win.sum() != 1:
                    self.log_warning('Object w/o clear winner in {0}'.format(
                                                                self.fieldid))

                # index of the winner
                winnerID = sourceID[win.nonzero()[0][0]]

                # Update cache
                for candidate in sourceID[sourceID > 0][1:]:
                    self.cache_set(candidate, winnerID)

            primaryID.append(winnerID)

        self.log_info('Finished identifying primaryIDs')

        matchinfo = {'nObs': np.array(nObs),
                     'primaryID': np.array(primaryID),
                     'partnerID':  np.array(partnerID),
                     'r2': np.array(partner_r),
                     'rErr2': np.array(partner_rErr),
                     'i2': np.array(partner_i),
                     'iErr2': np.array(partner_iErr),
                     'ha2': np.array(partner_ha),
                     'haErr2': np.array(partner_haErr),
                     'errBits2': np.array(partner_errBits)}
        return (matchdata['sourceID_1'], matchinfo)


###########
# FUNCTIONS
###########

def run_strip(strip):
    """Seams the fields in a given longitude strip."""
    # Strips are defined by the start longitude of a 10 deg-wide strip
    assert(strip in np.arange(25, 215+1, constants.STRIPWIDTH))
    log.info('{0}: strip{1}: START'.format(str(datetime.datetime.now())[0:19],
                                           strip))
    # Intialize caching dictionary
    CACHE[strip] = {}

    # Which are our boundaries?
    # Note: we must allow FIELD_MAXDIST for border overlaps!
    lon1 = strip - constants.FIELD_MAXDIST
    lon2 = strip + constants.STRIPWIDTH + constants.FIELD_MAXDIST

    cond_strip = (constants.IPHASQC_COND_RELEASE
                  & (IPHASQC['l'] >= lon1)
                  & (IPHASQC['l'] < lon2))
    n_fields = cond_strip.sum()  # How many fields are in our strip?
    n_processed = 0

    # Testcase in strip 215:
    #cond_strip = (IPHASQC['id'] == '4035_oct2009') | (IPHASQC['id'] == '4035o_oct2009')

    # Seam fields; do the best-seeing fields first!
    for idx in np.argsort(IPHASQC['seeing_max']):
        if cond_strip[idx]:
            n_processed += 1
            log.info('{0}: strip{1}: {2}/{3}: seaming {4}'.format(
                                        str(datetime.datetime.now())[0:19],
                                        strip,
                                        n_processed,
                                        n_fields,
                                        IPHASQC['id'][idx]))
            log.info('{0}: strip{1}: cached {2:.2f} GB'.format(
                                        str(datetime.datetime.now())[0:19],
                                        strip,
                                        sys.getsizeof(CACHE[strip])/(1024**3)))

            try:
                s = SeamMachine(IPHASQC['id'][idx],
                                IPHASQC['ra'][idx],
                                IPHASQC['dec'][idx],
                                strip)
                s.run()
            except SeamingException, e:
                log.error(str(e))
            except Exception, e:
                log.error('strip %s: %s: *UNEXPECTED EXCEPTION*: %s' % (
                                                strip, IPHASQC['id'][idx], e))

    del CACHE[strip]  # Clear cache
    # We're done
    log.info('{0}: strip{1}: ENDED'.format(str(datetime.datetime.now())[0:19],
                                           strip))


def run_all(lon1=25, lon2=215, ncores=2):
    """Seam the fields in all 10-degree wide longitude strips.

    Be aware that the densest strips need ~8 GB RAM each.
    Set ncores to avoid swapping at all cost!
    """
    strips = np.arange(lon1, lon2+0.1, constants.STRIPWIDTH)
    log.info('Seaming in longitude strips %s' % (strips))

    # Distribute the work over ncores
    p = Pool(processes=ncores)
    results = p.imap(run_strip, strips)
    for i in results:
        pass


###################
# MAIN EXECUTION
###################

if __name__ == "__main__":

    log.setLevel('INFO')

    # Which longitude range to process?
    if len(sys.argv) > 1:
        strip = int(sys.argv[1])
        run_strip(strip)
    else:
        print('Give the strip number as the first argument')