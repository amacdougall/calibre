# Customizations on the `customized` branch

This branch adds Japanese reading support and Anki card mining to the calibre
**E-book viewer**. Everything else in calibre is unchanged. This file covers
only what differs from upstream; for general calibre development, see the
upstream docs.

## Setup

### 1. Run calibre from source

Use calibre's standard "develop from source" method: install the official
binary, then point it at this checkout. You need both. The checkout has
calibre's Python and RapydScript source but no runtime. The installed app
supplies the frozen Python, Qt/PyQt6 with QtWebEngine, the compiled C
extensions (`calibre_extensions`), and the other native dependencies.
`CALIBRE_DEVELOP_FROM` makes that runtime load calibre's code from this
checkout instead of from its own bundle. Every change on this branch is
Python or RapydScript, so no compilation is needed.

Building a fully standalone calibre from the repo is possible on Linux (see
the upstream `bypy/` tooling and the "Setting up a calibre development
environment" manual page). It's a heavy job, and on macOS it isn't practical.

- **Install the official calibre build that matches the source version.** The
  binary supplies the compiled C extensions, so its version must match
  `numeric_version` in `src/calibre/constants.py`, which is **9.10.0** on this
  branch. If the versions don't match, startup fails with errors like
  `ModuleNotFoundError: No module named 'calibre_extensions.…'`. Older
  releases are available at <https://download.calibre-ebook.com/>.
- Launch with:

  ```sh
  export CALIBRE_DEVELOP_FROM=/path/to/this/checkout/src
  calibre-debug -g
  # macOS: /Applications/calibre.app/Contents/MacOS/calibre-debug -g
  ```

  The viewer's frontend is RapydScript (`src/pyj/*.pyj`). When run this way,
  it's recompiled each time the viewer starts ("Compiling viewer code..."), so
  you don't need a build step. Restart after editing `.pyj` files.

Running from source uses your normal calibre config directory and library,
the same ones the installed app uses.

### 2. Dictionary and frequency data: nothing to download

The offline dictionary is **committed to the repo** at
`src/calibre/gui2/viewer/anki_cards/jmdict.sqlite` (~48 MB). It contains:

- **JMdict** (English edition, EDRDG, CC BY-SA 4.0): ~217k entries
- **JPDB v2.2 frequency ranks** (Kana edition, via Kuuuube's Yomitan
  dictionaries): ~274k forms

Both are baked in at build time, so there are no runtime downloads and no
network access is needed. To rebuild or refresh it (optional; stdlib Python,
no dependencies):

```sh
cd src/calibre/gui2/viewer/anki_cards
python3 build_jmdict_sqlite.py --download      # fetches JMdict_e.gz + JPDB zip
python3 build_jmdict_sqlite.py --input ~/Downloads/JMdict_e.gz   # or use a local copy
python3 jmdict.py 食べる                        # quick sanity check
```

Build provenance is stored in the DB's `meta` table.

### 3. Anki (only needed for card mining)

- Anki must be **running** with the
  [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (code
  `2055492159`). The viewer talks to `http://127.0.0.1:8765`.
- Cards use the **Kaishi 1.5k** note type, with fields `Word`, `Sentence`,
  `Word Reading`, and `Word Meaning`. The note type must already exist in
  your collection. The easiest way to get it is to import the
  [Kaishi 1.5k](https://github.com/donkuri/Kaishi) deck. To
  use a different note type, edit `MODEL_NAME` and the field constants in
  `anki_cards/kaishi.py`.
- The target deck must already exist. Set it in the viewer under
  **Preferences → Anki integration** (see below). Out of the box, cards go to
  Anki's own `Default` deck.

## Features

### Add word + sentence to Anki: `Ctrl/Cmd+Shift+A`

1. Select a word in the book. Japanese has no word boundaries, so select it
   by hand. A partial inflection is fine: 擦ら still finds 擦る.
2. Press **Ctrl+Shift+A** (macOS: **Cmd+Shift+A**).
3. An **Add to Anki** popup lists the JMdict matches. Each shows the reading,
   the deinflection path (e.g. `← past`), the JPDB frequency rank, the part
   of speech, and the glosses. The list is ordered roughly as jisho.org
   orders it.
   - **Enter** picks the top candidate. **Esc** cancels.
   - The **word field is editable**. Type to look up again (after a 500 ms
     pause). The sentence stays as it was when the popup opened.
   - **Mine raw selection** (at the bottom) adds the word exactly as typed,
     with no dictionary data.
4. The card is created with Word, Reading, Meaning, and the **surrounding
   sentence**. The sentence boundary detection is aware of 「」 and （）, so a
   quote inside a sentence doesn't cut it off early. Furigana is removed from
   the sentence.

Duplicates are checked **across all decks**. If the word already exists, you
see "already in Anki" and no card is added.

### Furigana modes: `Ctrl/Cmd+Shift+R`

This shortcut cycles between three modes: **show all**, **rare words only**,
and **hide all**. The current mode briefly appears on screen.

- **Rare words only** hides ruby on words whose JPDB rank is better (lower)
  than the threshold, which defaults to **10,000**. Ruby stays visible on
  rarer words.
- Hidden ruby uses `visibility: hidden`, so the text doesn't reflow or
  repaginate.
- Ruby with no hiragana is never hidden. This covers katakana glosses and
  傍点 emphasis dots.
- This is a heuristic and is roughly 90% accurate. Ranks are per word, not
  per spelling, so a common word written in rare kanji counts as common.

Configure the mode and threshold under **Preferences → Furigana**.

### Reader preferences: Anki integration

Open with `Ctrl+,` / `Cmd+,`, then choose **Anki integration**. Settings:

- **Default deck** for mined cards.
- **Deck for this book**: a per-book override. Leave it blank to use the
  default. It's keyed by library ID + book ID, so it survives title or author
  edits. Books opened outside a library are keyed by file path.
- **Omit speaker tags** (per book): removes leading speaker labels such as
  `筆者　…` from mined sentences. Useful for interview or dialogue formats.

### JMdict in the Lookup panel

The Lookup panel (`Ctrl+L`, or select text and click *Lookup*) has a new
source, **JMdict (offline)**. It uses the same entries, deinflection, and
frequency ranks as the Anki popup. Common words (rank ≤ 10,000) are
highlighted.

- The panel now has an **editable query field**, so you can type a word
  directly. Selecting text in the book still fills it in.
- If you've customized your lookup sources before, the new default won't
  show up on its own. Add it from the source editor (choose **Type: JMdict
  (offline dictionary)**) or click **Restore defaults**.

### Clean text selection and copying

Selecting or copying text now **removes furigana and inline formatting**,
which applies everywhere, not just to Anki mining. Copying 漢字(かんじ) gives
漢字.

### Per-image color inversion

Right-click (or long-press) an image and choose **Invert image colors**. This
is useful for black-on-white diagrams in a dark color scheme. With the
inversion, white paper becomes the theme's background color rather than pure
black. The setting is saved per image as a new annotation type
(`image_invert`), so it persists and syncs like highlights do.

> Caveat: stock calibre doesn't recognize the `image_invert` annotation type.
> If you share a library between this branch and an unmodified calibre, the
> stock build may fail when merging annotations for a book that has inverted
> images.

### macOS: floating docks stay visible

On macOS, floating viewer docks (Lookup, TOC, etc.) no longer hide when the
viewer window loses focus. This means you can keep the Lookup panel open
next to another app.

## Keyboard shortcuts (new)

| Action                              | Windows/Linux     | macOS            |
|-------------------------------------|-------------------|------------------|
| Add selected word + sentence to Anki| `Ctrl+Shift+A`    | `Cmd+Shift+A`    |
| Cycle furigana mode                 | `Ctrl+Shift+R`    | `Cmd+Shift+R`    |
| *In the Add to Anki popup:* accept top match | `Enter` | `Enter`          |
| *In the Add to Anki popup:* cancel  | `Esc`             | `Esc`            |

Both shortcuts can be changed under **Preferences → Keyboard shortcuts**.
Don't use `Cmd+A`: it's select-all, which selects the whole book.

## Code map

| Area | Files |
|------|-------|
| Dictionary, deinflection, AnkiConnect (plain stdlib Python, no calibre imports, runnable from the CLI) | `src/calibre/gui2/viewer/anki_cards/` |
| Sentence extraction (JS side) | `src/pyj/read_book/anki_sentence.pyj` (Python twin with tests: `anki_cards/sentence_extraction_reference.py`) |
| Furigana | `src/pyj/read_book/furigana.pyj`, `anki_cards/furigana.py` |
| Preference panels | `src/pyj/read_book/prefs/anki.pyj`, `prefs/furigana.pyj` |
| Viewer ↔ Python bridge | `src/calibre/gui2/viewer/ui.py`, `web_view.py`, `src/pyj/viewer-main.pyj`, `read_book/view.pyj`, `read_book/iframe.pyj` |
| Lookup panel | `src/calibre/gui2/viewer/lookup.py` |
