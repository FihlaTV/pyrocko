from __future__ import print_function, absolute_import

import os
import time
import unittest
import tempfile
import shutil

import numpy as num

from . import common
from pyrocko import squirrel, util, pile, io


class SquirrelTestCase(unittest.TestCase):

    test_files = [
        ('test1.mseed', 'mseed'),
        ('test2.mseed', 'mseed'),
        ('test1.sac', 'sac'),
        ('test1.stationxml', 'stationxml'),
        ('test2.stationxml', 'stationxml'),
        ('test1.stations', 'pyrocko_stations'),
        ('test1.cube', 'datacube')]

    def test_detect(self):
        for (fn, format) in SquirrelTestCase.test_files:
            fpath = common.test_data_file(fn)
            self.assertEqual(format, squirrel.detect_format(fpath))

    def test_load(self):
        ii = 0
        for (fn, format) in SquirrelTestCase.test_files:
            fpath = common.test_data_file(fn)
            for nut in squirrel.iload(fpath, content=[]):
                ii += 1

        assert ii == 396

        ii = 0
        sq = squirrel.Squirrel()
        for (fn, _) in SquirrelTestCase.test_files:
            fpath = common.test_data_file(fn)
            for nut in squirrel.iload(fpath, content=[], squirrel=sq):
                ii += 1

        assert ii == 396

        ii = 0
        for (fn, _) in SquirrelTestCase.test_files:
            fpath = common.test_data_file(fn)
            for nut in squirrel.iload(fpath, content=[], squirrel=sq):
                ii += 1

        ii = 0
        for (fn, _) in SquirrelTestCase.test_files:
            fpath = common.test_data_file(fn)
            for nut in squirrel.iload(fpath, squirrel=sq):
                ii += 1

        assert ii == 396

        fpaths = [
            common.test_data_file(fn)
            for (fn, _) in SquirrelTestCase.test_files]

        ii = 0
        for nut in squirrel.iload(fpaths, content=[], squirrel=sq):
            ii += 1

        assert ii == 396

    def test_query_mtimes(self):
        fpaths = [
            common.test_data_file(fn)
            for (fn, _) in SquirrelTestCase.test_files]

        sq = squirrel.Squirrel()
        for nut in squirrel.iload(fpaths, squirrel=sq, content=[]):
            pass

        mtimes_ref = dict(
            (fpath, os.stat(fpath)[8]) for fpath in fpaths)

        def check(fpaths, mtimes):
            for fpath, mtime in zip(fpaths, mtimes):
                self.assertEqual(mtimes_ref.get(fpath, None), mtime)

        fpaths1 = fpaths + ['nonexistent']
        mtimes = sq.get_mtimes(fpaths)
        check(fpaths, mtimes)

        fpaths2 = fpaths1[::-2]
        mtimes2 = sq.get_mtimes(fpaths2)
        check(fpaths2, mtimes2)

        mtimes3 = [sq.get_mtime(fpath) for fpath in fpaths1]
        check(fpaths1, mtimes3)

    def test_dig_undig(self):
        nuts = []
        for file_name in 'abcde':
            for file_element in range(2):
                nuts.append(squirrel.Nut(
                    file_name=file_name,
                    file_format='test',
                    file_mtime=0.0,
                    file_segment=0,
                    file_element=file_element,
                    kind='test'))

        sq = squirrel.Squirrel()
        sq.dig(nuts)

        data = []
        for file_name in 'abcde':
            nuts2 = sq.undig(file_name)
            for nut in nuts2:
                data.append((nut.file_name, nut.file_element))
        self.assertEqual(
            [(file_name, i) for file_name in 'abcde' for i in range(2)],
            data)

        data = []
        for fn, nuts2 in sq.undig_many(filenames=['a', 'c']):
            for nut in nuts2:
                data.append((nut.file_name, nut.file_element))

        self.assertEqual(
            [(file_name, i) for file_name in 'ac' for i in range(2)],
            data)

    def test_tdelta(self):
        sq = squirrel.squirrel
        for tdelta in sq.tdelta_edges:
            print(tdelta, tdelta / 3600., tdelta / (3600*24))

    def benchmark_chop(self):
        n = 100000

        tmin_g = util.stt('2000-01-01 00:00:00')
        tmax_g = util.stt('2020-01-01 00:00:00')

        txs = num.sort(num.random.uniform(tmin_g, tmax_g, n+1))

        def make_nuts():
            for it in range(txs.size-1):
                file_name = 'file_%i' % it
                tmin = txs[it]
                tmax = txs[it+1]
                tmin_seconds, tmin_offset = squirrel.model.tsplit(tmin)
                tmax_seconds, tmax_offset = squirrel.model.tsplit(tmax)
                for file_element in range(10):
                    nut = squirrel.Nut(
                        file_name=file_name,
                        file_format='test',
                        file_mtime=0.0,
                        file_segment=0,
                        file_element=file_element,
                        codes='c%02i' % file_element,
                        tmin_seconds=tmin_seconds,
                        tmin_offset=tmin_offset,
                        tmax_seconds=tmax_seconds,
                        tmax_offset=tmax_offset,
                        kind='test')

                    yield nut

        dbfilename = '/tmp/squirrel_benchmark_chop.db'

        filldb = not os.path.exists(dbfilename)

        sq = squirrel.Squirrel(dbfilename)
        ts = []
        if filldb:
            ts.append(time.time())
            sq.dig(make_nuts())
            sq.commit()
            ts.append(time.time())
            print('init db: %g' % (ts[-1] - ts[-2]))

        ts.append(time.time())
        it = 0
        for fn, nuts in sq.undig_all():
            it += 1

        assert it == n

        ts.append(time.time())
        print('undig all: %g' % (ts[-1] - ts[-2]))

        tmin, tmax = sq.tspan()
        print('   ', tmin, tmax)

        ts.append(time.time())
        print('squirrel, tspan: %g' % (ts[-1] - ts[-2]))

        for kind, codes in sq.iter_codes():
            print('   %s: %s' % (kind, codes))

        ts.append(time.time())
        print('squirrel, codes: %g' % (ts[-1] - ts[-2]))

        t = tmin_g
        tinc = 3600*24
        while t < tmax:
            t += tinc
            tmin = t
            tmax = t + tinc

            sq.undig_span_naiv(tmin, tmax)
            break

        ts.append(time.time())
        print('squirrel, undig_span_naiv: %g' % (ts[-1] - ts[-2]))

        t = tmin_g
        tinc = 3600*24
        while t < tmax:
            t += tinc
            tmin = t
            tmax = t + tinc

            sq.undig_span(tmin, tmax)
            break

        ts.append(time.time())
        print('squirrel, undig_span: %g' % (ts[-1] - ts[-2]))

    def benchmark_load(self):
        dir = '/tmp/testdataset_d'
        if not os.path.exists(dir):
            common.make_dataset(dir, tinc=36., tlen=1*common.D)

        fns = sorted(util.select_files([dir], show_progress=False))

        ts = []

        if False:
            cachedirname = tempfile.mkdtemp('testcache')

            ts.append(time.time())
            pile.make_pile(
                fns, fileformat='detect', cachedirname=cachedirname,
                show_progress=False)

            ts.append(time.time())
            print('pile, initial scan: %g' % (ts[-1] - ts[-2]))

            pile.make_pile(
                fns, fileformat='detect', cachedirname=cachedirname,
                show_progress=False)

            ts.append(time.time())
            print('pile, rescan: %g' % (ts[-1] - ts[-2]))

            shutil.rmtree(cachedirname)

        if True:
            ts.append(time.time())
            ii = 0
            for fn in fns:
                for tr in io.load(fn, getdata=True):
                    ii += 1

            ts.append(time.time())
            print('plain load baseline: %g' % (ts[-1] - ts[-2]))

        if True:
            ts.append(time.time())

            ii = 0
            for nut in squirrel.iload(fns, content=[]):
                ii += 1

            assert ii == len(fns)

            ts.append(time.time())
            print('squirrel, no db: %g' % (ts[-1] - ts[-2]))

        dbfilename = '/tmp/squirrel.db'
        if os.path.exists(dbfilename):
            os.unlink(dbfilename)
        sq = squirrel.Squirrel(dbfilename)

        ts.append(time.time())
        ii = 0
        for nut in squirrel.iload(fns, content=[], squirrel=sq):
            ii += 1

        assert ii == len(fns)
        ts.append(time.time())
        print('squirrel, initial scan: %g' % (ts[-1] - ts[-2]))

        print('xxx', sq.tspan())

        ts.append(time.time())
        print('squirrel, tspan: %g' % (ts[-1] - ts[-2]))

        for kind, codes in sq.iter_codes():
            print('   %s: %s' % (kind, codes))

        ts.append(time.time())
        print('squirrel, codes: %g' % (ts[-1] - ts[-2]))

        ii = 0
        for nut in squirrel.iload(fns, content=[], squirrel=sq):
            ii += 1

        assert ii == len(fns)
        ts.append(time.time())
        print('squirrel, rescan: %g' % (ts[-1] - ts[-2]))

        ii = 0
        for nut in squirrel.iload(fns, content=[], squirrel=sq,
                                  check_mtime=False):
            ii += 1

        assert ii == len(fns)
        ts.append(time.time())
        print('squirrel, rescan, no mtime check: %g' % (ts[-1] - ts[-2]))

        ii = 0
        for nut in squirrel.iload(fns, content=[], squirrel=sq,
                                  skip_up_to_date=True, check_mtime=False):
            ii += 1

        assert ii == 0
        ts.append(time.time())
        print('squirrel, rescan, index only (skip up to date): %g' % (
            ts[-1] - ts[-2]))

        ii = 0
        for fn, nuts in sq.undig_many(fns):
            ii += 1

        assert ii == len(fns)
        ts.append(time.time())
        print('squirrel, pure undig: %g' % (ts[-1] - ts[-2]))

        sq.choose(fns)

        ts.append(time.time())
        print('squirrel, choose: %g' % (ts[-1] - ts[-2]))

        for fn in fns:
            sq.get_mtime(fn)

        ts.append(time.time())
        print('squirrel, query mtime (file-by-file): %g' % (ts[-1] - ts[-2]))

        sq.get_mtimes(fns)

        ts.append(time.time())
        print('squirrel, query mtime (batch): %g' % (ts[-1] - ts[-2]))

        sq.add(fns)

        print(sq.global_selection)

    def test_source(self):

        tmin = util.str_to_time('2018-01-01 00:00:00')
        tmax = util.str_to_time('2018-01-02 00:00:00')
        sq = squirrel.Squirrel()
        sq.add_fdsn_site('geofon')
        for s in sq.stations():
            print(s)


if __name__ == "__main__":
    util.setup_logging('test_catalog', 'info')
    unittest.main()
