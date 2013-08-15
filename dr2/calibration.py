#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fits a global photometric calibration using the Glazebrook algorithm.

The algorithm finds a set of zeropoint shifts which minimizes the magnitude
offsets between overlapping exposures (computed using the dr2.offsets module.)
In addition, the APASS survey is used to ensure that we do not deviate from
the 'absolute truth'.

This file also contains a class to apply the calibration to the catalogues.
"""
import numpy as np
import os
from scipy import sparse
from scipy.sparse import linalg
from astropy.io import ascii
from astropy.io import fits
from astropy import log

import constants
from constants import IPHASQC
from constants import IPHASQC_COND_RELEASE
import util

__author__ = 'Geert Barentsen'
__copyright__ = 'Copyright, The Authors'
__credits__ = ['Geert Barentsen', 'Hywel Farnhill', 'Janet Drew']


PATH_UNCALIBRATED = os.path.join(constants.DESTINATION,
                                 'bandmerged')
PATH_CALIBRATED = os.path.join(constants.DESTINATION,
                               'bandmerged-calibrated')


# When to trust other surveys?
TOLERANCE = 0.03
MIN_MATCHES = 30

# Extra anchors selected in the final phases of the data release,
# when a few areas with poor anchor coverage were spotted

# Upper left corner
EXTRA_ANCHORS = ['4510_jul2004a', '4510o_jul2004a',
                 '4583_jul2009', '4583o_jul2009',
                 '4525_jul2004a', '4525o_jul2004a',
                 '4598_jul2004a', '4598o_jul2004a',
                 '4087_jun2004', '4087o_jun2004',
                 '4093_jun2004', '4093o_jun2004',
                 '4084_jun2007', '4084o_jun2007',
                 '4106_jun2007', '4106o_jun2007',
                 '4085_jul2009', '4085o_jul2009',
                 '4140_jun2004', '4140o_jun2004',
                 '4128_jun2004', '4128o_jun2004',
                 '4102_jun2004', '4102o_jun2004',
                 '4139_jun2004', '4139o_jun2004',
                 '4118_jun2004', '4118o_jun2004',
                 '4088_jun2004', '4088o_jun2004',
                 '4089_jun2004', '4089o_jun2004',
                 '4091_jun2004', '4091o_jun2004',
                 '4092_jun2004', '4092o_jun2004',
                 '4103_jun2007', '4103o_jun2007',
                 '4114_jun2004', '4114o_jun2004',
                 '4088_jun2004', '4088o_jun2004',
                 '4099_jun2004', '4099o_jun2004',
                 '4110_jun2004', '4110o_jun2004',
                 '4096_jun2004', '4096o_jun2004',
                 '4127_jun2004', '4127o_jun2004',
                 '6094_oct2003', '6094o_oct2003',
                 '4116_jun2004', '4116o_jun2004',
                 '4120_jun2004', '4120o_jun2004']
# Lower left corner
EXTRA_ANCHORS.append('4480_jul2004a')
EXTRA_ANCHORS.append('4480o_jul2004a')

# Other pars
EXTRA_ANCHORS.append('5023_jul2009')
EXTRA_ANCHORS.append('5023o_jul2009')
EXTRA_ANCHORS.append('5265_sep2003')
EXTRA_ANCHORS.append('5265o_sep2003')
EXTRA_ANCHORS.append('5535_jul2009')
EXTRA_ANCHORS.append('5535o_jul2009')
EXTRA_ANCHORS.append('5471_jul2007')
EXTRA_ANCHORS.append('5471o_jul2007')
EXTRA_ANCHORS.append('5393_jun2005')
EXTRA_ANCHORS.append('5393o_jun2005')

ANCHOR_NIGHTS = [20031214, 20070629, 20050711, 20050714, 20061214, 20090701,
                 20070630, 20061129, 20070627, 20061130, 20080724, 20050710,
                 20080829, 20051024, 20031101, 20070624, 20051101, 20031117,
                 20051101, 20051023, 20050723, 20041030, 20100802]

# Make sure the following runs are no anchors
# cf. e-mail Janet to Geert, 13 Aug 2013
ANCHOR_BLACKLIST = ['0546_oct2003', '0546o_oct2003',
                    '0555_oct2003', '0555o_oct2003',
                    '0886_nov2003b', '0886o_nov2003b',
                    '6459_aug2004a', '6459o_aug2004a',
                    '5056_jun2005', '5056o_jun2005',
                    '5063_jun2005', '5063o_jun2005',
                    '2627_nov2006d', '2627o_nov2006d',
                    '2635_nov2006d', '2635o_nov2006d',
                    '2654_nov2006d', '2654o_nov2006d',
                    '5239_may2007', '5239o_may2007',
                    '3886_dec2007', '3886o_dec2007',
                    '0010_nov2008', '0010o_nov2008',
                    '0012_nov2008', '0012o_nov2008',
                    '0332_nov2008', '0332o_nov2008',
                    '0334_nov2008', '0334o_nov2008',
                    '0911_aug2009', '0911o_aug2009',
                    '0825_aug2009', '0825o_aug2009',
                    ]



def calibrate_band(band='r'):
    """Calibrate a single band.

    Parameters
    ----------
    band : one of 'r', 'i', 'ha'

    Returns
    -------
    cal : Calibration class
        object entailing the shifts to be added (cal.shifts)
    """
    log.info('Starting to calibrate the {0} band'.format(band))

    # H-alpha is a special case because the APASS-based selection of anchors
    # is not possible
    if band == 'ha':
        # We use the r-band calibration as the baseline for H-alpha
        rcalib_file = os.path.join(constants.DESTINATION,
                                   'calibration',
                                   'calibration-r.csv')
        rcalib = ascii.read(rcalib_file)
        cal = Calibration(band)
        cal.shifts = rcalib['shift']

        # We do run one iteration of Glazebrook using special H-alpha anchors
        overlaps = cal.get_overlaps()
        solver = Glazebrook(cal.runs, overlaps, cal.anchors)
        solver.solve()
        cal.add_shifts( solver.get_shifts() )
        cal.evaluate('glazebrook', 
                     'H-alpha calibration shifts'.format(band))

    else:
    
        cal = Calibration(band)
        cal.evaluate('step1', 
                     '{0} - uncalibrated'.format(band))

        # Glazebrook: first pass (minimizes overlap offsets)
        overlaps = cal.get_overlaps()
        solver = Glazebrook(cal.runs, overlaps, cal.anchors)
        solver.solve()
        cal.add_shifts( solver.get_shifts() )
        cal.evaluate('step2',
                     '{0} - step 2: Glazebrook pass 1'.format(band))    
        
        # Correct outliers against APASS and fix them as anchors
        delta = np.abs(cal.apass_shifts - cal.shifts)
        cond_extra_anchors = ((cal.apass_matches >= MIN_MATCHES) &
                              -np.isnan(delta) &
                              (delta >= TOLERANCE)
                             )
        log.info('Adding {0} extra anchors'.format(cond_extra_anchors.sum()))
        idx_extra_anchors = np.where(cond_extra_anchors)
        cal.anchors[idx_extra_anchors] = True
        cal.shifts[idx_extra_anchors] = cal.apass_shifts[idx_extra_anchors]
        cal.evaluate('step3',
                     '{0} - step 3: added {1} extra anchors'.format(
                                       band, cond_extra_anchors.sum()))

        # Run Glazebrook again with the newly added anchors
        overlaps = cal.get_overlaps()
        solver = Glazebrook(cal.runs, overlaps, cal.anchors)    
        solver.solve()
        cal.add_shifts( solver.get_shifts() )
        cal.evaluate('step4',
                     '{0} - step 4 - Glazebrook pass 2'.format(band))

        # Write the used anchors to a csv file
        anchor_list_filename = os.path.join(constants.DESTINATION,
                                           'calibration',
                                           'anchors-{0}.csv'.format(band))
        solver.write_anchor_list(anchor_list_filename)
        

    filename = os.path.join(constants.DESTINATION,
                            'calibration',
                            'calibration-{0}.csv'.format(band))
    cal.write(filename)

    return cal


def calibrate():
    """Calibrates all bands in the survey.

    Produces files called "calibration{r,i,ha}.csv" which tabulate
    the zeropoint shifts to be *added* to each exposure.
    """
    # Make sure the output directory exists
    util.setup_dir(os.path.join(constants.DESTINATION, 'calibration'))
    # Calibrate each band in the survey
    for band in constants.BANDS:
        calibrate_band(band)




class Calibration(object):
    """Container for calibration information in a single band.

    This class holds information about the offsets between overlaps, 
    the offsets against other surveys, the choice of anchor fields, 
    and the calibration shifts required.

    This class is effectively a container to hold everything we know
    about our survey zeropoints, and contains several functions to 
    interact with this information (e.g. create spatial plots of zeropoint 
    offsets.)

    Attributes
    ----------
    band : {'r', 'i', 'ha'}
    runs : array of int
        List of exposure numbers for `band` which are part of the data release.
    shifts : array of float
        The calibration shifts to be *added* to the magnitudes of `runs`.
    anchors : array of bool
        Which exposures can be trusted?
    """

    def __init__(self, band):
        """Loads the necessary information about the survey zeropoints.

        Parameters
        ----------
        band : string
            One of 'r', 'i', 'ha'.
        """
        #self.calib = np.array(zip(runs, np.zeros(len(runs))),
        #                      dtype=[('runs', 'i4'), ('shifts', 'f4')])
        assert(band in constants.BANDS)
        self.band = band

        self.runs = IPHASQC['run_'+band][IPHASQC_COND_RELEASE]
        self.shifts = np.zeros(len(self.runs))  # Shifts to be *ADDED* - init to 0

        # Load broad-band comparison data
        if band in ['r', 'i']:
            self.apass_shifts = IPHASQC[band+'shift_apassdr7'][IPHASQC_COND_RELEASE]
            self.apass_matches = IPHASQC[band+'match_apassdr7'][IPHASQC_COND_RELEASE]
            self.sdss_shifts = IPHASQC[band+'shift_sdss'][IPHASQC_COND_RELEASE]
            self.sdss_matches = IPHASQC[band+'match_sdss'][IPHASQC_COND_RELEASE]
        else:
            self.apass_shifts = np.zeros(len(self.runs))
            self.apass_matches = np.zeros(len(self.runs))

        assert(len(self.runs) == len(self.shifts))
        assert(len(self.runs) == len(self.apass_shifts))
        assert(len(self.apass_shifts) == len(self.apass_matches))

        self._load_offsetdata()
        self.anchors = self.select_anchors()

    def add_shifts(self, shifts):
        self.shifts += shifts

    def get_shift(self, run):
        """Returns the calibrations shift for a given run.

        Parameters
        ----------
        run : integer
            Exosure identifier for which you want to know the calibration shift.

        Returns
        -------
        shift : float
            Shift to be *added* to the magnitudes of the specified run.
        """
        return self.shifts[self.runs == run][0]

    def evaluate(self, name, title):
        # Plot the absolute calibration shifts
        l = IPHASQC['l'][IPHASQC_COND_RELEASE]
        b = IPHASQC['b'][IPHASQC_COND_RELEASE]
        self._spatial_plot(l, b, self.shifts, 'calib-'+name, 'Calibration '+title)

        if self.band in ['r', 'i']:
            statsfile = os.path.join(constants.DESTINATION,
                                     'calibration',
                                     'stats-{0}.txt'.format(self.band))
            with open(statsfile, 'w') as out:
                # Against APASS
                mask_use = (self.apass_matches >= MIN_MATCHES)
                l = IPHASQC['l'][IPHASQC_COND_RELEASE][mask_use]
                b = IPHASQC['b'][IPHASQC_COND_RELEASE][mask_use]
                delta = self.apass_shifts[mask_use] - self.shifts[mask_use]
                self._spatial_plot(l, b, delta, 'apass-'+name, 'APASS: '+title)

                stats =  "mean={0:.3f}+/-{1:.3f}, ".format(np.mean(delta),
                                                           np.std(delta))
                stats += "min/max={0:.3f}/{1:.3f}".format(np.min(delta),
                                                          np.max(delta))

                out.write(stats)
                log.info(stats)

                # Against SDSS
                mask_use = (self.sdss_matches >= MIN_MATCHES)
                l = IPHASQC['l'][IPHASQC_COND_RELEASE][mask_use]
                b = IPHASQC['b'][IPHASQC_COND_RELEASE][mask_use]
                delta = self.sdss_shifts[mask_use] - self.shifts[mask_use]
                self._spatial_plot(l, b, delta, 'sdss-'+name, 'SDSS '+title)


    def _spatial_plot(self, l, b, shifts, name, title=''):
        """Creates a spatial plot of l/b against shifts."""
        import matplotlib
        matplotlib.use('Agg')  # Cluster does not have an X backend
        from matplotlib import pyplot as plt

        fig = plt.figure(figsize=(12,6))
        fig.subplots_adjust(0.06, 0.15, 0.97, 0.9)
        p = fig.add_subplot(111)
        p.set_title(title)
        scat = p.scatter(l, b, c=shifts, vmin=-0.13, vmax=+0.13,
                         edgecolors='none',
                         s=7, marker='h')
        plt.colorbar(scat)
        # Indicate anchors
        p.scatter(IPHASQC['l'][IPHASQC_COND_RELEASE][self.anchors],
                  IPHASQC['b'][IPHASQC_COND_RELEASE][self.anchors],
                  edgecolors='black', facecolor='none',
                  s=15, marker='x', alpha=0.9, lw=0.3)
        p.set_xlim([28, 217])
        p.set_ylim([-5.2, +5.2])
        p.set_xlabel('l')
        p.set_ylabel('b')

        path = os.path.join(constants.DESTINATION,
                            'calibration',
                            self.band+'-'+name+'.png')
        fig.savefig(path, dpi=200)
        log.info('Wrote {0}'.format(path))

        plt.close()
        return fig

    def write(self, filename):
        """Writes calibration shifts to a CSV file on disk.

        Parameters
        ----------
        filename : string
            Filename of the CSV file to write the calibration shifts.
        """
        log.info('Writing results to {0}'.format(filename))
        f = open(filename, 'w')
        f.write('run,shift\n')
        for myrun, myshift in zip(self.runs, self.shifts):
            f.write('{0},{1}\n'.format(myrun, myshift))
        f.close()

    def _load_offsetdata(self):
        filename_offsets = os.path.join(constants.DESTINATION,
                                        'calibration',
                                        'offsets-{0}.csv'.format(self.band))
        log.info('Reading {0}'.format(filename_offsets))
        mydata = ascii.read(filename_offsets)
        # Do not use the offsets unless enough stars were used
        #mask_use = (mydata['n'] >= 5) & (mydata['std'] < 0.07)
        self.offsetdata = mydata #[mask_use]

    def get_overlaps(self, weights=True):
        """Returns a dict with the magnitude offsets between run overlaps.

        Takes the current calibration into account.
        """
        log.info('Loading calibration-corrected magnitude offsets between overlaps')
        # Performance optimisation
        current_shifts = dict(zip(self.runs, self.shifts))

        # Dictionary of field overlaps
        overlaps = {}
        for row in self.offsetdata:
            myrun1 = row['run1']
            myrun2 = row['run2']
            if myrun1 in self.runs and myrun2 in self.runs:
                # Offset is computed as (run1 - run2), hence correcting 
                # for calibration means adding (shift_run1 - shift_run2)
                myoffset = (row['offset'] 
                            + current_shifts[myrun1]
                            - current_shifts[myrun2])
                if myrun1 not in overlaps:
                    overlaps[myrun1] = {'runs': [], 'offsets': [], 'weights': []}
                overlaps[myrun1]['runs'].append(myrun2)
                overlaps[myrun1]['offsets'].append(myoffset)

                if weights:
                    overlaps[myrun1]['weights'].append(np.sqrt(row['n']))
                else:
                    overlaps[myrun1]['weights'].append(1.0)

        return overlaps

    def select_anchors(self):
        """Returns a boolean array indicating which runs are suitable anchors."""
        median_pair_offset = -0.008 
        IS_STABLE = ( (IPHASQC.field('med_dr') < (median_pair_offset+0.03)) &
                      (IPHASQC.field('med_dr') > (median_pair_offset-0.03)) &
                      (IPHASQC.field('med_di') < (median_pair_offset+0.03)) &
                      (IPHASQC.field('med_di') > (median_pair_offset-0.03)) &
                      (IPHASQC.field('med_dh') < (median_pair_offset+0.03)) &
                      (IPHASQC.field('med_dh') > (median_pair_offset-0.03))
                    )

        if self.band == 'ha':
            # Because the H-alpha calibration is tied to the r-band,
            # we require fields to be "stable" to be an anchor in H-alpha.
            # "Stable" is defined as the fieldpair not showing great shifts.
            anchors = IS_STABLE[IPHASQC_COND_RELEASE]
            log.info('IS_STABLE: {0} fields are H-alpha anchors'.format(anchors.sum()))
            return anchors

        else:
            tolerance = 0.03  # Default = 0.03
            min_matches = 30  # Default = 20
            anchors = []
            IS_APASS_ANCHOR = ( (IPHASQC.field('rmatch_apassdr7') >= min_matches)
                              & (IPHASQC.field('imatch_apassdr7') >= min_matches)
                              & (np.abs(IPHASQC.field('rshift_apassdr7')) <= tolerance)
                              & (np.abs(IPHASQC.field('ishift_apassdr7')) <= tolerance)
                              & (np.abs(IPHASQC.field('rshift_apassdr7') - IPHASQC.field('ishift_apassdr7')) <= tolerance) )

            IS_OLD_ANCHOR = (IPHASQC.field('anchor') == 1)
            IS_EXTRA_ANCHOR = np.array([myfield in EXTRA_ANCHORS 
                                        for myfield in IPHASQC.field('id')])
            IS_BLACKLIST = np.array([myfield in ANCHOR_BLACKLIST 
                                     for myfield in IPHASQC.field('id')])

            IS_EXTRA_NIGHT = np.array([mynight in ANCHOR_NIGHTS 
                                       for mynight in IPHASQC.field('night')])

            anchors = (-IS_BLACKLIST &
                       IS_STABLE & 
                       (IS_OLD_ANCHOR | IS_EXTRA_ANCHOR | IS_EXTRA_NIGHT | IS_APASS_ANCHOR)
                      )
            result = anchors[IPHASQC_COND_RELEASE]

            log.info('IS_APASS_ANCHOR: {0} fields'.format(IS_APASS_ANCHOR.sum()))
            log.info('IS_OLD_ANCHOR: {0} fields'.format(IS_OLD_ANCHOR.sum()))
            log.info('IS_EXTRA_ANCHOR: {0} fields'.format(IS_EXTRA_ANCHOR.sum()))
            log.info('IS_EXTRA_NIGHT: {0} fields'.format(IS_EXTRA_NIGHT.sum()))
            log.info('IS_BLACKLIST: {0} fields'.format(IS_BLACKLIST.sum()))            
            log.info('Anchors in data release: {0} fields'.format(result.sum()))

            return result


#############
# GLAZEBROOK
#############

class Glazebrook(object):
    """Finds zeropoints which minimise the offsets between overlapping fields.

    This class allows a set of catalogues with independently derived zeropoints
    to be brought to a global calibration with minimal magnitude offsets
    where fields overlap.

    This is achieved using the method detailed in the paper by 
    Glazebrook et al. 1994 (http://adsabs.harvard.edu/abs/1994MNRAS.266...65G).
    In brief, a set of equations are set up which allow the magnitude offsets
    between field overlaps to be minimised in a least squares sense.

    This class uses sparse matrix functions (scipy.sparse) to solve the large
    matrix equation in an efficient fashion.
    """

    def __init__(self, runs, overlaps, anchors):
        self.runs = runs
        self.overlaps = overlaps
        self.anchors = anchors
        self.nonanchors = ~anchors
        self.n_nonanchors = self.nonanchors.sum()
        log.info('Glazebrook: there are {0} runs ({1} are anchors)'.format(len(runs),
                                                               anchors.sum()))

    def _A(self):
        """Returns the matrix called "A" in [Glazebrook 1994, Section 3.3]
        """
        log.info('Glazebrook: creating a sparse {0}x{0} matrix (might take a while)'.format(self.n_nonanchors))
        A = sparse.lil_matrix((self.n_nonanchors,
                               self.n_nonanchors))

        nonanchorruns = self.runs[self.nonanchors]
        # Loop over all non-anchors that make up the matrix
        for i, run in enumerate(nonanchorruns):
            
            try:
                # On the diagonal, the matrix holds the negative sum of weights
                A[i, i] = -float(np.sum(self.overlaps[run]['weights']))

                # Off the diagonal, the matrix holds the weight where two runs overlap
                for run2, weight in zip(self.overlaps[run]['runs'],
                                        self.overlaps[run]['weights']):
                    idx_run2 = np.argwhere(run2 == nonanchorruns)
                    if len(idx_run2) > 0:
                        j = idx_run2[0]  # Index of the overlapping run
                        A[i, j] = weight
                        A[j, i] = weight  # Symmetric matrix
            except KeyError:
                log.warning('Glazebrook: no overlap data for run {0}'.format(run))
                A[i, i] = -1.0
        return A

    def _b(self):
        """Returns the vector called "b" in [Glazebrook 1994, Section 3.3]
        """
        b = np.zeros(self.n_nonanchors)
        for i, run in enumerate(self.runs[self.nonanchors]):
            try:
                b[i] = np.sum(
                              np.array(self.overlaps[run]['offsets']) *
                              np.array(self.overlaps[run]['weights'])
                              )
            except KeyError:
                log.warning('Glazebrook: no overlap data for run {0}'.format(run))
                b[1] = 0.0
        return b

    def solve(self):
        """Returns the solution of the matrix equation.
        """
        self.A = self._A()
        self.b = self._b()
        log.info('Glazebrook: now solving the matrix equation')
        # Note: there may be alternative algorithms
        # which are faster for symmetric matrices.
        self.solution = linalg.lsqr(self.A, self.b,
                                    atol=1e-8, iter_lim=2e5, show=False)
        log.info('Glazebrook: solution found')
        log.info('Glazebrook: mean shift = {0} +/- {1}'.format(
                                            np.mean(self.solution[0]),
                                            np.std(self.solution[0])))
        return self.solution

    def get_shifts(self):
        shifts = np.zeros(len(self.runs))
        shifts[self.nonanchors] = self.solution[0]
        return shifts

    def write_anchor_list(self, filename):
        with open(filename, 'w') as out:
            out.write('run,is_anchor\n')
            for i in range(len(self.runs)):
                out.write('{0},{1}\n'.format(self.runs[i], self.anchors[i]))


class CalibrationApplicator(object):
    """Applies the calibration to the catalogues.

    This class will read a bandmerged catalogue from the 'bandmerged' directory,
    apply the appropriate calibration shifts as listed in 
    'calibration/calibration-{r,i,ha}.csv', and then write the updated 
    catalogue to a new directory 'bandmerged-calibrated'."""

    def __init__(self):
        self.datadir = PATH_UNCALIBRATED
        self.outdir = PATH_CALIBRATED
        util.setup_dir(self.outdir)

        # Read in the calibration
        self.calib = {}
        for band in constants.BANDS:
            calib_file = os.path.join(constants.DESTINATION,
                                      'calibration',
                                      'calibration-{0}.csv'.format(band))
            self.calib[band] = ascii.read(calib_file)

    def run(self, filename):
        #for filename in os.listdir(self.datadir):
        log.info('Correcting {0}'.format(filename))
        self.calibrate(filename)

    def get_shifts(self, filename):
        fieldid = filename.split('.fits')[0]
        idx_field = np.argwhere(IPHASQC.field('id') == fieldid)[0]

        shifts = {}
        for band in constants.BANDS:
            cond_run = (self.calib[band]['run']
                        == IPHASQC.field('run_' + band)[idx_field])
            if cond_run.sum() > 0:
                shifts[band] = self.calib[band]['shift'][cond_run][0]
            else:
                log.warning('No shift for %s' % filename)
                shifts[band] = 0.0

        log.info("Shifts for {0}: {1}".format(fieldid, shifts))
        return shifts

    def calibrate(self, filename):
        path_in = os.path.join(self.datadir, filename)
        path_out = os.path.join(self.outdir, filename)
        shifts = self.get_shifts(filename)

        param = {'stilts': constants.STILTS,
                 'filename_in': path_in,
                 'filename_out': path_out,
                 'cmd': """'replacecol r "toFloat(r  + {r})"; \
                            replacecol rPeakMag "toFloat(rPeakMag  + {r})"; \
                            replacecol rAperMag1 "toFloat(rAperMag1  + {r})"; \
                            replacecol rAperMag3 "toFloat(rAperMag3  + {r})"; \
                            replacecol i "toFloat(i  + {i})"; \
                            replacecol iPeakMag "toFloat(iPeakMag  + {i})"; \
                            replacecol iAperMag1 "toFloat(iAperMag1  + {i})"; \
                            replacecol iAperMag3 "toFloat(iAperMag3  + {i})"; \
                            replacecol ha "toFloat(ha  + {ha})"; \
                            replacecol haPeakMag "toFloat(haPeakMag  + {ha})"; \
                            replacecol haAperMag1 "toFloat(haAperMag1  + {ha})"; \
                            replacecol haAperMag3 "toFloat(haAperMag3  + {ha})"; \
                            replacecol rmi "toFloat(r-i)"; \
                            replacecol rmha "toFloat(r-ha)"; \
                           '""".format(**shifts)}

        cmd = '{stilts} tpipe cmd={cmd} in={filename_in} out={filename_out}'.format(**param)
        log.debug(cmd)
        status = os.system(cmd)
        log.info('stilts status: '+str(status))
        return status


def calibrate_one(filename):
    """Applies the photometric re-calibration to a single bandmerged field catalogue."""
    with log.log_to_file(os.path.join(constants.LOGDIR, 'apply_calibration.log')):
        try:
            ca = CalibrationApplicator()
            ca.run(filename)
        except Exception, e:
            log.error('%s: *UNEXPECTED EXCEPTION*: calibrate_one: %s' % (filename, e))
        return filename


def apply_calibration(clusterview):
    """Applies the photometric re-calibration to all bandmerged field catalogues."""
    filenames = os.listdir(PATH_UNCALIBRATED)
    results = clusterview.imap(calibrate_one, filenames)

    # Print a friendly message once in a while
    i = 0
    for filename in results:
        i += 1
        if (i % 1000) == 0:
            log.info('Completed file {0}/{1}'.format(i, len(filenames)))
    log.info('Application of calibration finished')


def median_colours_one(path):
    """Returns the median of a single band-merged catalogue."""
    mydata = fits.getdata(path, 1)
    mask_reliable = mydata['reliable']
    fieldid = path.split('/')[-1].split('.')[-2]
    median_rmi = np.median(mydata['rmi'][mask_reliable])
    median_rmha = np.median(mydata['rmha'][mask_reliable])
    return (fieldid, median_rmi, median_rmha)


def compute_median_colours(clusterview,
                           directory=PATH_UNCALIBRATED,
                           output_filename = os.path.join(
                                                   constants.DESTINATION,
                                                  'calibration',
                                                  'median-colours.csv')):
    """Computes the median r-Ha colour in all fields.

    This will be used as an input to evaluate the H-alpha calibration.
    """
    log.info('Starting to compute median(r-ha) values.')

    # Start the work on the cluster
    util.setup_dir(os.path.join(constants.DESTINATION, 'calibration'))
    paths = [os.path.join(directory, filename) 
             for filename in os.listdir(directory)]
    results = clusterview.imap(median_colours_one, paths)

    # Write the results to a csv file
    with open(output_filename, 'w') as out:
        out.write('field,median_rmi,median_rmha\n')
        for (field, median_rmi, median_rmha) in results:
            out.write('{0},{1},{2}\n'.format(field,
                                             median_rmi,
                                             median_rmha))

    log.info('Computing median(r-ha) values finished.')    


################################
# MAIN EXECUTION (FOR DEBUGGING)
#################################

if __name__ == '__main__':
    log.setLevel('DEBUG')
    calibrate()
    #calibrate_band('ha')
