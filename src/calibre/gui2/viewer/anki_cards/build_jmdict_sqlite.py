#!/usr/bin/env python3
# Offline build step: parse JMdict_e into the compact jmdict.sqlite that the
# reader bundles for word lookup. Run once (and again whenever you want to
# refresh the dictionary); the resulting jmdict.sqlite is committed to the repo.
#
#   # download + build in one go (English-only edition):
#   python3 build_jmdict_sqlite.py --download
#
#   # or point at a file you already have (gz or plain XML):
#   python3 build_jmdict_sqlite.py --input ~/Downloads/JMdict_e.gz
#
# Stdlib only. JMdict is from the EDRDG project
# (https://www.edrdg.org/jmdict/j_jmdict.html), distributed under CC BY-SA 4.0.
#
# We keep only what lookup needs: headwords (kanji), readings (kana), and per
# sense the part-of-speech codes + English glosses. That keeps the DB small and
# the schema trivial to query with stdlib sqlite3 (see jmdict.py).

import argparse
import gzip
import hashlib
import io
import os
import re
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

JMDICT_E_URL = 'http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz'
DEFAULT_OUTPUT = os.path.join(os.path.dirname(__file__), 'jmdict.sqlite')

# JMdict's pos/misc fields are XML entity refs (&v1;, &n;, ...) whose DTD
# replacement text is a long human description ("Ichidan verb"). We want the
# short code, so before parsing we rewrite every entity to expand to its own
# name: <!ENTITY v1 "Ichidan verb"> becomes <!ENTITY v1 "v1">, after which expat
# resolves &v1; to the literal "v1".
_ENTITY_DEF = re.compile(rb'<!ENTITY\s+(\S+)\s+"[^"]*"\s*>')

UNIT_SEP = '\x1f'  # field-internal list separator (matches kaishi.py convention)


def _read_bytes(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rb') as f:
        return f.read()


def _rewrite_entities(data):
    return _ENTITY_DEF.sub(rb'<!ENTITY \1 "\1">', data)


def _iter_entries(xml_bytes):
    '''Yield (ent_seq, kanji[], readings[], senses[]) from JMdict XML bytes.

    Each sense is (pos[], glosses[]); a sense with no <pos> inherits the
    previous sense's pos, as JMdict specifies.
    '''
    for _event, elem in ET.iterparse(io.BytesIO(xml_bytes), events=('end',)):
        if elem.tag != 'entry':
            continue
        ent_seq = int(elem.findtext('ent_seq'))
        kanji = [k.text for k in elem.findall('k_ele/keb') if k.text]
        readings = [r.text for r in elem.findall('r_ele/reb') if r.text]
        senses = []
        last_pos = []
        for sense in elem.findall('sense'):
            pos = [p.text for p in sense.findall('pos') if p.text]
            if pos:
                last_pos = pos
            else:
                pos = last_pos
            glosses = [g.text for g in sense.findall('gloss') if g.text]
            if glosses:
                senses.append((pos, glosses))
        yield ent_seq, kanji, readings, senses
        elem.clear()  # keep memory flat across the ~200k entries


def build(input_path, output_path):
    raw = _read_bytes(input_path)
    source_sha256 = hashlib.sha256(raw).hexdigest()
    xml_bytes = _rewrite_entities(raw)

    if os.path.exists(output_path):
        os.remove(output_path)
    conn = sqlite3.connect(output_path)
    try:
        cur = conn.cursor()
        cur.executescript('''
            CREATE TABLE entry (ent_seq INTEGER PRIMARY KEY);
            CREATE TABLE kanji (ent_seq INTEGER, text TEXT);
            CREATE TABLE reading (ent_seq INTEGER, text TEXT);
            CREATE TABLE sense (ent_seq INTEGER, ord INTEGER, pos TEXT, gloss TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        ''')

        n_entries = 0
        for ent_seq, kanji, readings, senses in _iter_entries(xml_bytes):
            n_entries += 1
            cur.execute('INSERT INTO entry (ent_seq) VALUES (?)', (ent_seq,))
            cur.executemany('INSERT INTO kanji (ent_seq, text) VALUES (?, ?)',
                            [(ent_seq, k) for k in kanji])
            cur.executemany('INSERT INTO reading (ent_seq, text) VALUES (?, ?)',
                            [(ent_seq, r) for r in readings])
            cur.executemany(
                'INSERT INTO sense (ent_seq, ord, pos, gloss) VALUES (?, ?, ?, ?)',
                [(ent_seq, i, UNIT_SEP.join(pos), UNIT_SEP.join(glosses))
                 for i, (pos, glosses) in enumerate(senses)])

        cur.executescript('''
            CREATE INDEX idx_kanji_text ON kanji (text);
            CREATE INDEX idx_reading_text ON reading (text);
            CREATE INDEX idx_sense_ent ON sense (ent_seq);
        ''')
        cur.executemany('INSERT INTO meta (key, value) VALUES (?, ?)', [
            ('source_sha256', source_sha256),
            ('source_url', JMDICT_E_URL),
            ('built_at', time.strftime('%Y-%m-%dT%H:%M:%S')),
            ('entry_count', str(n_entries)),
        ])
        conn.commit()
        cur.execute('VACUUM')
        conn.commit()
    finally:
        conn.close()

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f'Built {output_path}')
    print(f'  entries: {n_entries}')
    print(f'  source sha256: {source_sha256}')
    print(f'  size: {size_mb:.1f} MiB')


def main():
    p = argparse.ArgumentParser(description='Build jmdict.sqlite from JMdict_e.')
    p.add_argument('--input', help='Path to JMdict_e (.gz or plain XML).')
    p.add_argument('--download', action='store_true',
                   help=f'Download JMdict_e from {JMDICT_E_URL} into a temp file first.')
    p.add_argument('--output', default=DEFAULT_OUTPUT,
                   help='Output sqlite path (defaults next to this script).')
    args = p.parse_args()

    input_path = args.input
    if args.download:
        input_path = os.path.join(os.path.dirname(args.output) or '.', 'JMdict_e.gz')
        print(f'Downloading {JMDICT_E_URL} -> {input_path} ...')
        urllib.request.urlretrieve(JMDICT_E_URL, input_path)
    if not input_path:
        p.error('provide --input PATH or --download')

    build(input_path, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
