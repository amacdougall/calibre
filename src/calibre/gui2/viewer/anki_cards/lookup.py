# Dictionary lookup orchestrator: deinflect a selection, match it against
# JMdict, and return the candidate dictionary entries to show the user. This is
# the Python analogue of HoshiReader's native lookup(text, maxResults, scanLength).
#
# Stdlib only, no Calibre imports, so it is exercisable from the CLI:
#   python3 lookup.py 食べている
#   python3 lookup.py            # runs the self-test

import sys

# Dual-mode imports: relative when loaded as a Calibre submodule
# (calibre.gui2.viewer.anki_cards.lookup), bare when run as a CLI script from
# inside this directory (python3 lookup.py ...).
try:
    from .deinflect import deinflect, rules_compatible
    from .jmdict import JMdict
except ImportError:
    from deinflect import deinflect, rules_compatible
    from jmdict import JMdict

MAX_RESULTS = 16
# When the whole selection finds nothing, retry on slightly shorter leading
# prefixes to absorb an over-selected trailing particle (が, を, から, ...).
# Kept small on purpose: trimming aggressively would let any leading-kana run
# match some short common word and flood the popup with noise.
TRAILING_TRIM = 3


def _meaning_text(senses):
    '''Flatten an entry's senses into a single '; '-joined gloss string.'''
    glosses = []
    for s in senses:
        glosses.extend(s['glosses'])
    return '; '.join(glosses)


def _flat_pos(senses):
    '''Unique part-of-speech tags across senses, order preserved.'''
    seen = {}
    for s in senses:
        for p in s['pos']:
            seen[p] = True
    return list(seen.keys())


def _candidate_entry(entry, reasons, matched_term):
    '''Shape a JMdict entry (+ the deinflection chain that found it) into the
    JSON-able dict that crosses the bridge and drives the popup.

    ``matched_term`` is the (deinflected) form that hit this entry; when it is
    one of the entry's readings we surface that reading as primary, so a lookup
    by kana shows the reading the user actually selected.
    '''
    kanji = entry['kanji']
    readings = entry['readings']
    senses = entry['senses']
    glosses = [g for s in senses for g in s['glosses']]
    if matched_term in readings:
        primary_reading = matched_term
    else:
        primary_reading = readings[0] if readings else ''
    return {
        'ent_seq': entry['ent_seq'],
        'word': kanji[0] if kanji else (readings[0] if readings else ''),
        'reading': primary_reading,
        'readings': readings,
        'pos': _flat_pos(senses),
        'meaning': _meaning_text(senses),
        'glosses': glosses,
        'reasons': reasons,
    }


def _collect_for_surface(db, surface, best):
    '''Deinflect ``surface``, look each candidate up, and merge matches into
    ``best`` (ent_seq -> (chain_len, candidate)), keeping the shortest
    deinflection chain per entry.'''
    for d in deinflect(surface):
        for entry in db.lookup_exact(d.term):
            if not rules_compatible(d.rules, _flat_pos(entry['senses'])):
                continue
            ent_seq = entry['ent_seq']
            chain_len = len(d.reasons)
            if ent_seq not in best or chain_len < best[ent_seq][0]:
                best[ent_seq] = (chain_len, _candidate_entry(entry, d.reasons, d.term))


def lookup_candidates(selection, max_results=MAX_RESULTS, db=None):
    '''Return candidate dictionary entries for a selected string.

    Tries the full selection (raw form first, then its deinflections); only if
    nothing matches does it fall back to shorter leading prefixes. Returns a
    list of candidate dicts ordered surface-form / shortest-chain first, deduped
    by ent_seq, capped at ``max_results``. Empty list if nothing matches.
    '''
    surface = (selection or '').strip()
    if not surface:
        return []
    db = db or JMdict()

    best = {}
    min_len = max(1, len(surface) - TRAILING_TRIM)
    for length in range(len(surface), min_len - 1, -1):
        _collect_for_surface(db, surface[:length], best)
        if best:
            # Found matches at this (longest possible) prefix; don't shorten further.
            break

    ordered = sorted(best.values(), key=lambda t: t[0])  # by deinflection-chain length
    return [cand for _chain_len, cand in ordered][:max_results]


def _selftest():
    db = JMdict()

    def words(sel):
        return [c['word'] for c in lookup_candidates(sel, db=db)]

    checks = [
        ('食べている', '食べる'),
        ('美味しかった', '美味しい'),
        ('読まない', '読む'),
        ('行かなかった', '行く'),
        ('飲みたい', '飲む'),
        ('食べる', '食べる'),       # already dictionary form
    ]
    failures = 0
    for sel, expected in checks:
        got = words(sel)
        ok = expected in got
        failures += not ok
        print(f'{"ok " if ok else "FAIL"} {sel} -> {expected}  got={got[:6]}')

    # over-generation is filtered: 食ぶ (a bogus deinflection) must not appear
    assert '食ぶ' not in words('食べている'), '食ぶ should be filtered by JMdict'
    print('ok over-generated 食ぶ filtered out')

    # over-selected trailing particle is absorbed by the small prefix trim
    assert '食べる' in words('食べていますが'), 'trailing が should be trimmed'
    print('ok trailing-particle 食べていますが -> 食べる')

    # ambiguous: はし returns several distinct dictionary entries
    hashi = lookup_candidates('はし', db=db)
    assert len({c['ent_seq'] for c in hashi}) >= 2, 'はし should be ambiguous'
    print(f'ok はし -> {len(hashi)} candidates: {[c["word"] for c in hashi][:6]}')

    # non-Japanese input matches nothing
    assert lookup_candidates('xyzzy', db=db) == [], 'latin nonsense -> []'
    print('ok latin nonsense -> []')

    if failures:
        raise SystemExit(f'{failures} lookup case(s) failed')
    print('All lookup self-tests passed.')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        for c in lookup_candidates(sys.argv[1]):
            print(f"{c['word']}  [{c['reading']}]  ({', '.join(c['pos'])})  "
                  f"{c['meaning'][:80]}  reasons={c['reasons']}")
    else:
        _selftest()
