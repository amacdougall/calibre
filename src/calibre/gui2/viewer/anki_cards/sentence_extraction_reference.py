# Reference implementation + regression tests for bracket-depth-aware Japanese
# sentence extraction. This is the Python twin of the algorithm in
# src/pyj/read_book/anki_sentence.pyj (extract_sentence / trim_dangling_brackets);
# pyj is Python-syntax so the two stay line-for-line equivalent. Runnable without
# a Calibre build:  python3 sentence_extraction_reference.py

BRACKETS = {'「': '」', '『': '』', '（': '）', '(': ')', '【': '】',
            '〈': '〉', '《': '》', '〔': '〕', '｛': '｝', '{': '}', '［': '］', '[': ']'}
OPEN = set(BRACKETS.keys())
CLOSE = set(BRACKETS.values())
DELIMS = set('。！？.!?\n\r')
# chars allowed to be pulled in *after* a terminal delimiter (closing quotes, etc.)
TRAILING = set('。、！？…‥」』）)】〉》〕｝}］]')


def extract_sentence(text, offset):
    # --- scan backward to sentence start, ignoring delimiters inside brackets ---
    depth = 0
    start = 0
    i = offset - 1
    while i >= 0:
        ch = text[i]
        if ch in CLOSE:          # entering a quote from the back
            depth += 1
        elif ch in OPEN:         # leaving it
            depth = max(0, depth - 1)
        elif depth == 0 and ch in DELIMS:
            start = i + 1
            break
        i -= 1

    # --- scan forward to sentence end, ignoring delimiters inside brackets ---
    depth = 0
    end = len(text)
    i = offset
    while i < len(text):
        ch = text[i]
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0 and ch in DELIMS:
            end = i + 1
            while end < len(text) and text[end] in TRAILING:
                end += 1
            break
        i += 1

    return trim_dangling_brackets(text[start:end].strip())


def trim_dangling_brackets(s):
    # Depth-aware scanning can land between bracket pairs (multi-sentence quotes),
    # leaving a stray 「 at the front or 」 at the back. Strip any bracket char at
    # an edge that has no partner inside the slice.
    changed = True
    while changed and len(s) > 1:
        changed = False
        first = s[0]
        if first in CLOSE or (first in OPEN and BRACKETS[first] not in s[1:]):
            s = s[1:].strip(); changed = True; continue
        last = s[-1]
        if last in OPEN:
            s = s[:-1].strip(); changed = True; continue
        if last in CLOSE:
            opener = next(o for o, c in BRACKETS.items() if c == last)
            if opener not in s[:-1]:
                s = s[:-1].strip(); changed = True; continue
    return s


CASES = [
    # (paragraph, selected substring, label)
    ('田中さんは「ちょっと待って！」と言った。', '言った', 'word AFTER internal-quote'),
    ('田中さんは「ちょっと待って！」と言った。', '待って', 'word INSIDE quote'),
    ('田中さんは「ちょっと待って！」と言った。', '田中', 'word BEFORE quote'),
    ('猫が好きだ。犬も好きだ。鳥はどうかな。', '犬', 'plain middle sentence'),
    ('「でもはいらないよ。とにかく明日は宿を探そう。だから、そんなに気を張らないでいいよ」', '気を張', 'HoshiReader case A'),
    ('「でもはいらないよ。とにかく明日は宿を探そう。だから、そんなに気を張らないでいいよ」', 'でも', 'HoshiReader case B'),
    ('彼は『吾輩は猫である。名前はまだ無い。』を読んだ。', '読んだ', 'nested book-title quote'),
]

for para, sub, label in CASES:
    off = para.index(sub)
    print(f'{label:28} | sel={sub!r:8} -> {extract_sentence(para, off)!r}')
