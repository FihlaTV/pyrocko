import os
import threading
import sqlite3
from collections import defaultdict, Counter

from pyrocko.io_common import FileLoadError
from pyrocko.squirrel import model, io
from pyrocko.squirrel.client import fdsn


def iitems(d):
    try:
        return d.iteritems()
    except AttributeError:
        return d.items()


icount = 0
lock = threading.Lock()


def make_unique_name():
    with lock:
        global icount
        name = '%i_%i' % (os.getpid(), icount)
        icount += 1

    return name


class Selection(object):
    def __init__(self, database):
        self.name = 'selection_' + make_unique_name()
        self._database = database
        self._conn = self._database.get_connection()
        self._sources = []

        self._names = {
            'db': 'temp',
            'file_states': self.name + '_file_states'}

        self._conn.execute(
            '''CREATE TEMP TABLE %(db)s.%(file_states)s (
                file_name text PRIMARY KEY,
                file_state integer)''' % self._names)

        self._database.selections.append(self)

    def database(self):
        return self._database

    def delete(self):
        self._conn.execute(
            'DROP TABLE %(db)s.%(file_states)s' % self._names)

        self._database.selections.remove(self)

    def add(self, filenames):
        self._conn.executemany(
            'INSERT INTO %(db)s.%(file_states)s VALUES (?, 0)' % self._names,
            ((s,) for s in filenames))

    def undig_grouped(self, skip_unchanged=False):

        if skip_unchanged:
            where = '''
                WHERE %(db)s.%(file_states)s.file_state == 0
            '''
        else:
            where = ''

        sql = ('''
            SELECT
                %(db)s.%(file_states)s.file_name,
                files.file_name,
                files.file_format,
                files.file_mtime,
                nuts.file_segment,
                nuts.file_element,
                kind_codes.kind,
                kind_codes.codes,
                nuts.tmin_seconds,
                nuts.tmin_offset,
                nuts.tmax_seconds,
                nuts.tmax_offset,
                nuts.deltat
            FROM %(db)s.%(file_states)s
            LEFT OUTER JOIN files
            ON %(db)s.%(file_states)s.file_name = files.file_name
            LEFT OUTER JOIN nuts
                ON files.rowid = nuts.file_id
            LEFT OUTER JOIN kind_codes
                ON nuts.kind_codes_id == kind_codes.rowid
        ''' + where + '''
            ORDER BY %(db)s.%(file_states)s.rowid
        ''') % self._names

        nuts = []
        fn = None
        for values in self._conn.execute(sql):
            if fn is not None and values[0] != fn:
                yield fn, nuts
                nuts = []

            if values[1] is not None:
                nuts.append(model.Nut(values_nocheck=values[1:]))

            fn = values[0]

        if fn is not None:
            yield fn, nuts

    def iter_mtimes(self):
        sql = '''
            SELECT files.file_name, files.file_format, files.file_mtime
            FROM %(db)s.%(file_states)s
            LEFT OUTER JOIN files
            ON %(db)s.%(file_states)s.file_name = files.file_name
            ORDER BY %(db)s.%(file_states)s.rowid
        ''' % self._names

        for row in self._conn.execute(sql):
            yield row

    def get_mtimes(self):
        return list(mtime for (_, _, mtime) in self.iter_mtimes())

    def flag_unchanged(self, check_mtime=True):

        def iter_filenames_states():
            for filename, fmt, mtime_db in self.iter_mtimes():
                if mtime_db is None or not os.path.exists(filename):
                    yield 0, filename
                    continue

                if check_mtime:
                    try:
                        mod = io.get_format_provider(fmt)
                        mtime_file = mod.mtime(filename)
                    except FileLoadError:
                        yield 0, filename
                        continue
                    except io.UnknownFormat:
                        yield 1, filename
                        continue

                    if mtime_db != mtime_file:
                        yield 0, filename
                        continue

                yield 1, filename

        sql = '''
            UPDATE %(db)s.%(file_states)s
            SET file_state = ?
            WHERE file_name = ?
        ''' % self._names

        self._conn.executemany(sql, iter_filenames_states())


class Squirrel(Selection):

    def __init__(self, database):
        Selection.__init__(self, database)
        c = self._conn

        self._names.update({
            'nuts': self.name + '_nuts'})

        c.execute(
            '''CREATE TEMP TABLE %(db)s.%(nuts)s (
                file_id integer,
                file_segment integer,
                file_element integer,
                kind_codes_id integer,
                tmin_seconds integer,
                tmin_offset float,
                tmax_seconds integer,
                tmax_offset float,
                deltat float,
                kscale integer,
                PRIMARY KEY (file_id, file_segment, file_element))
            ''' % self._names)

        c.execute(
            '''CREATE INDEX IF NOT EXISTS %(db)s.%(nuts)s_index_tmin_seconds
                ON %(nuts)s (tmin_seconds)
            ''' % self._names)

        c.execute(
            '''CREATE INDEX IF NOT EXISTS %(db)s.%(nuts)s_index_tmax_seconds
                ON %(nuts)s (tmax_seconds)''' % self._names)

        c.execute(
            '''CREATE INDEX IF NOT EXISTS %(db)s.%(nuts)s_index_kscale
                ON %(nuts)s (kscale, tmin_seconds)''' % self._names)

    def delete(self):
        self._conn.execute(
            'DROP TABLE %(db)s.%(nuts)s' % self._names)

        Selection.delete(self)

    def add(self, filenames, format='detect', check_mtime=True):

        Selection.add(self, filenames)
        if False:
            self._load(format, check_mtime)
        self._update_nuts()

    def _load(self, format, check_mtime):
        for _ in io.iload(
                self,
                content=[],
                skip_unchanged=True,
                format=format,
                check_mtime=check_mtime):
            pass

    def _update_nuts(self):
        c = self._conn
        c.execute(
            '''INSERT INTO %(db)s.%(nuts)s
                SELECT nuts.* FROM %(db)s.%(file_states)s
                INNER JOIN files
                ON %(db)s.%(file_states)s.file_name = files.file_name
                INNER JOIN nuts
                ON files.rowid = nuts.file_id
                WHERE %(db)s.%(file_states)s.file_state != 2
            ''' % self._names)

        c.execute(
            '''
            UPDATE %(db)s.%(file_states)s
            SET file_state = 2
            ''' % self._names)

    def add_fdsn_site(self, site):
        self._sources.append(fdsn.FDSNSource(site))

    def undig_span(self, tmin, tmax):
        tmin_seconds, tmin_offset = model.tsplit(tmin)
        tmax_seconds, tmax_offset = model.tsplit(tmax)

        tscale_edges = model.tscale_edges

        tmin_cond = []
        args = []
        for kscale in range(len(tscale_edges) + 1):
            if kscale != len(tscale_edges):
                tscale = tscale_edges[kscale]
                tmin_cond.append('''
                    (%(db)s.%(nuts)s.kscale == ?
                        AND %(db)s.%(nuts)s.tmin_seconds BETWEEN ? AND ?)
                ''')
                args.extend(
                    (kscale, tmax_seconds - tscale - 1, tmax_seconds + 1))

            else:
                tmin_cond.append('''
                    (%(db)s.%(nuts)s.kscale == ?
                        AND %(db)s.%(nuts)s.tmin_seconds <= ?)
                ''')

                args.extend(
                    (kscale, tmax_seconds + 1))

        sql = ('''
            SELECT
                files.file_name,
                files.file_format,
                files.file_mtime,
                %(db)s.%(nuts)s.file_segment,
                %(db)s.%(nuts)s.file_element,
                kind_codes.kind,
                kind_codes.codes,
                %(db)s.%(nuts)s.tmin_seconds,
                %(db)s.%(nuts)s.tmin_offset,
                %(db)s.%(nuts)s.tmax_seconds,
                %(db)s.%(nuts)s.tmax_offset,
                %(db)s.%(nuts)s.deltat
            FROM files
            INNER JOIN %(db)s.%(nuts)s
            ON files.rowid == %(db)s.%(nuts)s.file_id
            INNER JOIN kind_codes
            ON %(db)s.%(nuts)s.kind_codes_id == kind_codes.rowid
            WHERE ( ''' + ' OR '.join(tmin_cond) + ''')
                AND %(db)s.%(nuts)s.tmax_seconds >= ?
        ''') % self._names
        args.append(tmin_seconds)

        nuts = []
        for row in self._conn.execute(sql, args):
            nuts.append(model.Nut(values_nocheck=row))

        return nuts

    def undig_span_naiv(self, tmin, tmax):
        tmin_seconds, tmin_offset = model.tsplit(tmin)
        tmax_seconds, tmax_offset = model.tsplit(tmax)

        sql = '''
            SELECT
                files.file_name,
                files.file_format,
                files.file_mtime,
                %(db)s.%(nuts)s.file_segment,
                %(db)s.%(nuts)s.file_element,
                kind_codes.kind,
                kind_codes.codes,
                %(db)s.%(nuts)s.tmin_seconds,
                %(db)s.%(nuts)s.tmin_offset,
                %(db)s.%(nuts)s.tmax_seconds,
                %(db)s.%(nuts)s.tmax_offset,
                %(db)s.%(nuts)s.deltat
            FROM files
            INNER JOIN %(db)s.%(nuts)s
            ON files.rowid == %(db)s.%(nuts)s.file_id
            INNER JOIN kind_codes
            ON %(db)s.%(nuts)s.kind_codes_id == kind_codes.rowid
            WHERE %(db)s.%(nuts)s.tmax_seconds >= ?
                AND %(db)s.%(nuts)s.tmin_seconds <= ?
        ''' % self._names

        nuts = []
        for row in self._conn.execute(sql, (tmin_seconds, tmax_seconds+1)):
            nuts.append(model.Nut(values_nocheck=row))

        return nuts

    def tspan(self):
        sql = '''SELECT MIN(tmin_seconds) FROM %(db)s.%(nuts)s''' % self._names
        tmin = None
        for row in self._conn.execute(sql):
            tmin = row[0]

        tmax = None
        sql = '''SELECT MAX(tmax_seconds) FROM %(db)s.%(nuts)s''' % self._names
        for row in self._conn.execute(sql):
            tmax = row[0]

        return tmin, tmax

    def iter_codes(self, kind=None):
        sql = '''
            SELECT kind, codes from kind_codes
        '''
        for row in self._conn.execute(sql):
            yield row[0], row[1].split('\0')

    def update_channel_inventory(self, selection):
        for source in self._sources:
            source.update_channel_inventory(selection)
            for fn in source.get_channel_filenames(selection):
                self.add(fn)

    def __len__(self):
        sql = '''SELECT COUNT(*) FROM %(db)s.%(file_states)s''' % self._names
        for row in self._conn.execute(sql):
            return row[0]

    def __str__(self):
        return '''
squirrel selection "%s"
    files: %i''' % (self.name, len(self))

    def waveform(self, selection=None, **kwargs):
        pass

    def waveforms(self, selection=None, **kwargs):
        pass

    def station(self, selection=None, **kwargs):
        pass

    def stations(self, selection=None, **kwargs):
        self.update_channel_inventory(selection)

    def channel(self, selection=None, **kwargs):
        pass

    def channels(self, selection=None, **kwargs):
        pass

    def response(self, selection=None, **kwargs):
        pass

    def responses(self, selection=None, **kwargs):
        pass

    def event(self, selection=None, **kwargs):
        pass

    def events(self, selection=None, **kwargs):
        pass


class Database(object):
    def __init__(self, database=':memory:'):
        self._conn = sqlite3.connect(database)
        self._conn.text_factory = str
        self._initialize_db()
        self._need_commit = False
        self.selections = []

    def get_connection(self):
        return self._conn

    def _initialize_db(self):
        c = self._conn.cursor()
        c.execute(
            '''CREATE TABLE IF NOT EXISTS files (
                file_name text PRIMARY KEY,
                file_format text,
                file_mtime float)''')

        c.execute(
            '''CREATE TABLE IF NOT EXISTS nuts (
                file_id integer,
                file_segment integer,
                file_element integer,
                kind_codes_id text,
                tmin_seconds integer,
                tmin_offset float,
                tmax_seconds integer,
                tmax_offset float,
                deltat float,
                kscale integer,
                PRIMARY KEY (file_id, file_segment, file_element))''')

        c.execute(
            '''CREATE TABLE IF NOT EXISTS kind_codes (
                kind text,
                codes text,
                count integer,
                PRIMARY KEY (kind, codes))''')

        c.execute(
            '''CREATE INDEX IF NOT EXISTS index_nuts_file_id
                ON nuts (file_id)''')

        c.execute(
            '''CREATE TRIGGER IF NOT EXISTS delete_nuts
                BEFORE DELETE ON files FOR EACH ROW
                BEGIN
                  DELETE FROM nuts where file_id == old.rowid;
                END''')

        c.execute(
            '''CREATE TRIGGER IF NOT EXISTS decrement_kind_codes
                BEFORE DELETE ON nuts FOR EACH ROW
                BEGIN
                    UPDATE kind_codes
                    SET count = count - 1
                    WHERE old.kind_codes_id == rowid;
                END''')

        self._conn.commit()
        c.close()

    def dig(self, nuts):
        if not nuts:
            return

        c = self._conn.cursor()
        by_files = defaultdict(list)
        count_kind_codes = Counter()
        for nut in nuts:
            k = nut.file_name, nut.file_format, nut.file_mtime
            by_files[k].append(nut)
            count_kind_codes[nut.kind, nut.codes] += 1

        c.executemany(
            'INSERT OR IGNORE INTO kind_codes VALUES (?,?,0)',
            [kc for kc in count_kind_codes])

        c.executemany(
            '''
                UPDATE kind_codes
                SET count = count + ?
                WHERE kind == ? AND codes == ?
            ''',
            [(inc, kind, codes) for (kind, codes), inc
             in count_kind_codes.items()])

        for k, file_nuts in iitems(by_files):
            c.execute('DELETE FROM files WHERE file_name = ?', k[0:1])
            c.execute('INSERT INTO files VALUES (?,?,?)', k)
            file_id = c.lastrowid

            c.executemany(
                '''
                    INSERT INTO nuts VALUES
                        (?,?,?, (
                            SELECT rowid FROM kind_codes
                            WHERE kind == ? AND codes == ?
                         ), ?,?,?,?,?,?)
                ''',
                [(file_id, nut.file_segment, nut.file_element,
                  nut.kind, nut.codes,
                  nut.tmin_seconds, nut.tmin_offset,
                  nut.tmax_seconds, nut.tmax_offset,
                  nut.deltat, nut.kscale) for nut in file_nuts])

        self._need_commit = True
        c.close()

    def undig(self, filename):
        sql = '''
            SELECT
                files.file_name,
                files.file_format,
                files.file_mtime,
                nuts.file_segment,
                nuts.file_element,
                kind_codes.kind,
                kind_codes.codes,
                nuts.tmin_seconds,
                nuts.tmin_offset,
                nuts.tmax_seconds,
                nuts.tmax_offset,
                nuts.deltat
            FROM files
            INNER JOIN nuts ON files.rowid = nuts.file_id
            INNER JOIN kind_codes ON nuts.kind_codes_id == kind_codes.rowid
            WHERE file_name == ?'''

        return [model.Nut(values_nocheck=row)
                for row in self._conn.execute(sql, (filename,))]

    def undig_all(self):
        sql = '''
            SELECT
                files.file_name,
                files.file_format,
                files.file_mtime,
                nuts.file_segment,
                nuts.file_element,
                kind_codes.kind,
                kind_codes.codes,
                nuts.tmin_seconds,
                nuts.tmin_offset,
                nuts.tmax_seconds,
                nuts.tmax_offset,
                nuts.deltat
            FROM files
            INNER JOIN nuts ON files.rowid == nuts.file_id
            INNER JOIN kind_codes ON nuts.kind_codes_id == kind_codes.rowid
        '''

        nuts = []
        fn = None
        for values in self._conn.execute(sql):
            if fn is not None and values[0] != fn:
                yield fn, nuts
                nuts = []

            if values[1] is not None:
                nuts.append(model.Nut(values_nocheck=values))

            fn = values[0]

        if fn is not None:
            yield fn, nuts

    def undig_many(self, filenames):
        selection = self.new_selection(filenames)

        for fn, nuts in selection.undig_grouped():
            yield fn, nuts

        selection.delete()

    def get_mtime(self, filename):
        sql = '''
            SELECT file_mtime
            FROM files
            WHERE file_name = ?'''

        for row in self._conn.execute(sql, (filename,)):
            return row[0]

        return None

    def get_mtimes(self, filenames):
        selection = self.new_selection(filenames)
        mtimes = selection.get_mtimes()
        selection.delete()
        return mtimes

    def new_selection(self, filenames=None):
        selection = Selection(self)
        if filenames:
            selection.add(filenames)
        return selection

    def commit(self):
        if self._need_commit:
            self._conn.commit()
            self._need_commit = False

    def undig_content(self, nut):
        return None


if False:
    sq = Squirrel()
    sq.add('/path/to/data')
#    station = sq.add(Station(...))
#    waveform = sq.add(Waveform(...))

    station = model.Station()
    sq.remove(station)

    stations = sq.stations()
    for waveform in sq.waveforms(stations):
        resp = sq.response(waveform)
        resps = sq.responses(waveform)
        station = sq.station(waveform)
        channel = sq.channel(waveform)
        station = sq.station(channel)
        channels = sq.channels(station)
        responses = sq.responses(channel)
        lat, lon = sq.latlon(waveform)
        lat, lon = sq.latlon(station)
        dist = sq.distance(station, waveform)
        azi = sq.azimuth(channel, station)
