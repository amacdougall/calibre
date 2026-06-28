#!/usr/bin/env python3
# Smoke-test / reference CLI for the AnkiConnect path.
#
# Usage:
#   ./add_card.py --check                       # is AnkiConnect reachable?
#   ./add_card.py --lookup 食べている            # offline: show dictionary candidates
#   ./add_card.py --deck 専門用語 --word 教材 \
#       --sentence 日本語学習の教材を買った。   # add a real card
#
# --check / add require Anki running with the AnkiConnect add-on; --lookup is
# fully offline (just JMdict + the deinflector). This is deliberately tiny: it
# exists to prove the core capabilities end-to-end before any Calibre wiring.

import argparse
import sys

from anki_connect import AnkiConnect, AnkiConnectError, AnkiConnectUnavailable, build_note
from kaishi import MODEL_NAME, build_fields


def main():
    p = argparse.ArgumentParser(description='Add a Kaishi 1.5k card via AnkiConnect.')
    p.add_argument('--check', action='store_true', help='Only check AnkiConnect availability.')
    p.add_argument('--lookup', metavar='TEXT', help='Offline: print JMdict candidates for TEXT and exit.')
    p.add_argument('--deck', help='Target deck name.')
    p.add_argument('--word', help='Highlighted word (Word field).')
    p.add_argument('--sentence', default='', help='Surrounding sentence (Sentence field).')
    p.add_argument('--note', default='', help='Free-form note (Notes field).')
    p.add_argument('--allow-duplicate', action='store_true', help='Add even if a duplicate exists.')
    args = p.parse_args()

    if args.lookup:
        from lookup import lookup_candidates
        candidates = lookup_candidates(args.lookup)
        if not candidates:
            print(f'No dictionary matches for "{args.lookup}".')
            return 0
        for i, c in enumerate(candidates, 1):
            reasons = (' ← ' + ' '.join(c['reasons'])) if c['reasons'] else ''
            print(f'{i}. {c["word"]} [{c["reading"]}] '
                  f'({", ".join(c["pos"])}){reasons}\n   {c["meaning"][:100]}')
        return 0

    client = AnkiConnect()

    if args.check:
        try:
            print(f'AnkiConnect reachable, API version {client.version()}')
            print(f'Kaishi present: {MODEL_NAME in client.model_names()}')
            return 0
        except (AnkiConnectUnavailable, AnkiConnectError) as e:
            print(f'AnkiConnect not available: {e}', file=sys.stderr)
            return 1

    if not args.deck or not args.word:
        p.error('--deck and --word are required (unless using --check)')

    note = build_note(
        deck_name=args.deck,
        model_name=MODEL_NAME,
        fields=build_fields(word=args.word, sentence=args.sentence, note=args.note),
        allow_duplicate=args.allow_duplicate,
    )

    try:
        if not args.allow_duplicate and not client.can_add_note(note):
            print(f'Skipping "{args.word}": duplicate in deck (or otherwise unaddable).')
            return 0
        note_id = client.add_note(note)
        print(f'Added "{args.word}" -> note id {note_id}')
        return 0
    except (AnkiConnectUnavailable, AnkiConnectError) as e:
        print(f'Failed to add card: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
