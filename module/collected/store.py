"""
SQLite storage for collected stats.

The Alas process writes, the GUI process reads. Timestamps are unix epoch (UTC).
Keep this module free of Alas imports so it can be tested standalone.
"""
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource (
    ts REAL NOT NULL,
    instance TEXT NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_resource ON resource (instance, key, ts);
CREATE TABLE IF NOT EXISTS ship (
    ts REAL NOT NULL,
    instance TEXT NOT NULL,
    is_new INTEGER NOT NULL,
    campaign TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ship ON ship (instance, ts);
CREATE TABLE IF NOT EXISTS purchase (
    ts REAL NOT NULL,
    instance TEXT NOT NULL,
    shop TEXT NOT NULL,
    item TEXT NOT NULL,
    amount INTEGER NOT NULL,
    price INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT '',
    balance INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_purchase ON purchase (instance, ts);
CREATE TABLE IF NOT EXISTS hook (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    updated REAL NOT NULL,
    last_fired REAL
);
"""

# Record an unchanged value again only after this many seconds
MIN_INTERVAL = 600
# Values at or above this are checked for OCR jumps
JUMP_CHECK_MIN = 100
# Reject a new value that is more than this factor away from the previous one
JUMP_FACTOR = 5
# Ignore repeated GET_SHIP clicks on the same screen within this many seconds
SHIP_DEDUPE = 6


DEFAULT_PATH = './config/collected.db'


class CollectedStore:
    def __init__(self, path=None):
        if path is None:
            path = os.environ.get('ALAS_COLLECTED_DB', DEFAULT_PATH)
        self.path = path
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock, self.conn:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def _query(self, sql, *args):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _execute(self, sql, *args):
        with self.lock, self.conn:
            self.conn.execute(sql, args)

    """
    Writes
    """

    def record_resource(self, instance, key, value, source='', now=None):
        """
        Returns:
            bool: If recorded.
        """
        if not isinstance(value, (int, float)) or value <= 0:
            return False
        value = int(value)
        now = time.time() if now is None else now
        last = self._query(
            'SELECT ts, value FROM resource WHERE instance=? AND key=? ORDER BY ts DESC LIMIT 1',
            instance, key)
        if last:
            last_ts, last_value = last[0]['ts'], last[0]['value']
            if value == last_value and now - last_ts < MIN_INTERVAL:
                return False
            if last_value >= JUMP_CHECK_MIN and (
                    value * JUMP_FACTOR < last_value or value > last_value * JUMP_FACTOR):
                return False
        self._execute(
            'INSERT INTO resource (ts, instance, key, value, source) VALUES (?, ?, ?, ?, ?)',
            now, instance, key, value, source)
        return True

    def record_ship(self, instance, is_new, campaign='', image='', now=None):
        """
        Returns:
            bool: If recorded.
        """
        now = time.time() if now is None else now
        last = self._query('SELECT ts FROM ship WHERE instance=? ORDER BY ts DESC LIMIT 1', instance)
        if last and now - last[0]['ts'] < SHIP_DEDUPE:
            return False
        self._execute(
            'INSERT INTO ship (ts, instance, is_new, campaign, image) VALUES (?, ?, ?, ?, ?)',
            now, instance, int(bool(is_new)), campaign or '', image or '')
        return True

    def record_purchase(self, instance, shop, item, amount, price, currency='', balance=0, now=None):
        now = time.time() if now is None else now
        self._execute(
            'INSERT INTO purchase (ts, instance, shop, item, amount, price, currency, balance) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            now, instance, shop, item, int(amount or 0), int(price or 0), currency or '', int(balance or 0))

    def set_hook(self, name, status, detail='', now=None):
        now = time.time() if now is None else now
        self._execute(
            'INSERT INTO hook (name, status, detail, updated) VALUES (?, ?, ?, ?) '
            'ON CONFLICT(name) DO UPDATE SET status=excluded.status, detail=excluded.detail, '
            'updated=excluded.updated',
            name, status, detail, now)

    def touch_hook(self, name, now=None):
        now = time.time() if now is None else now
        self._execute('UPDATE hook SET last_fired=? WHERE name=?', now, name)

    """
    Reads
    """

    def instances(self):
        rows = self._query(
            'SELECT instance FROM resource UNION SELECT instance FROM ship '
            'UNION SELECT instance FROM purchase')
        return sorted(r['instance'] for r in rows)

    def _value_at_or_before(self, instance, key, ts):
        rows = self._query(
            'SELECT value FROM resource WHERE instance=? AND key=? AND ts<? ORDER BY ts DESC LIMIT 1',
            instance, key, ts)
        if rows:
            return rows[0]['value']
        rows = self._query(
            'SELECT value FROM resource WHERE instance=? AND key=? AND ts>=? ORDER BY ts ASC LIMIT 1',
            instance, key, ts)
        return rows[0]['value'] if rows else None

    def resource_summary(self, instance, day_start, week_start):
        """
        Returns:
            list[dict]: key, latest, latest_ts, first_ts, delta_today, delta_week, delta_all
        """
        out = []
        keys = self._query(
            'SELECT key, MIN(ts) AS first_ts, MAX(ts) AS latest_ts FROM resource '
            'WHERE instance=? GROUP BY key ORDER BY key', instance)
        for k in keys:
            key = k['key']
            latest = self._query(
                'SELECT value FROM resource WHERE instance=? AND key=? ORDER BY ts DESC LIMIT 1',
                instance, key)[0]['value']
            first = self._query(
                'SELECT value FROM resource WHERE instance=? AND key=? ORDER BY ts ASC LIMIT 1',
                instance, key)[0]['value']
            base_today = self._value_at_or_before(instance, key, day_start)
            base_week = self._value_at_or_before(instance, key, week_start)
            out.append({
                'key': key,
                'latest': latest,
                'latest_ts': k['latest_ts'],
                'first_ts': k['first_ts'],
                'delta_today': latest - base_today if base_today is not None else 0,
                'delta_week': latest - base_week if base_week is not None else 0,
                'delta_all': latest - first,
            })
        return out

    def resource_daily(self, instance, key, tz_offset_hours=0):
        """
        Returns:
            list[tuple[str, int]]: (YYYY-MM-DD, last value of that day), oldest first
        """
        offset = '{:+d} hours'.format(int(tz_offset_hours))
        rows = self._query(
            "SELECT date(ts, 'unixepoch', ?) AS day, value, ts FROM resource "
            "WHERE instance=? AND key=? ORDER BY ts ASC", offset, instance, key)
        daily = {}
        for r in rows:
            daily[r['day']] = r['value']
        return sorted(daily.items())

    def ships(self, instance, limit=50):
        return [dict(r) for r in self._query(
            'SELECT * FROM ship WHERE instance=? ORDER BY ts DESC LIMIT ?', instance, limit)]

    def ship_count(self, instance, since=0):
        rows = self._query(
            'SELECT COUNT(*) AS n, SUM(is_new) AS new FROM ship WHERE instance=? AND ts>=?', instance, since)
        return rows[0]['n'] or 0, rows[0]['new'] or 0

    def purchases(self, instance, limit=100):
        return [dict(r) for r in self._query(
            'SELECT * FROM purchase WHERE instance=? ORDER BY ts DESC LIMIT ?', instance, limit)]

    def purchase_totals(self, instance, since=0):
        return [dict(r) for r in self._query(
            'SELECT shop, item, currency, SUM(amount) AS amount, COUNT(*) AS times, '
            'SUM(price) AS spent, MAX(ts) AS last_ts FROM purchase WHERE instance=? AND ts>=? '
            'GROUP BY shop, item, currency ORDER BY last_ts DESC', instance, since)]

    def hooks(self):
        return [dict(r) for r in self._query('SELECT * FROM hook ORDER BY name')]
