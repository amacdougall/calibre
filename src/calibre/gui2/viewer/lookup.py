# License: GPL v3 Copyright: 2019, Kovid Goyal <kovid at kovidgoyal.net>

import json
import sys
import textwrap
from contextlib import suppress
from functools import lru_cache

from qt.core import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTime,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QNetworkCookie,
    QPalette,
    QPushButton,
    QSize,
    Qt,
    QTabWidget,
    QTimer,
    QUrl,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from qt.webengine import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineView

from calibre import prepare_string_for_xml, prints, random_user_agent
from calibre.ebooks.metadata.sources.search_engines import google_consent_cookies
from calibre.gui2 import error_dialog
from calibre.gui2.viewer.web_view import apply_font_settings, vprefs
from calibre.gui2.widgets2 import Dialog
from calibre.utils.config import tweaks
from calibre.utils.localization import _, canonicalize_lang, get_lang, lang_as_iso639_1
from calibre.utils.resources import get_path as P
from calibre.utils.webengine import create_script, insert_scripts, secure_webengine, setup_profile


@lru_cache
def lookup_lang():
    ans = canonicalize_lang(get_lang())
    if ans:
        ans = lang_as_iso639_1(ans) or ans
    return ans


special_processors = {}


def special_processor(func):
    special_processors[func.__name__] = func
    return func


@special_processor
def google_dictionary(word):
    ans = f'https://www.google.com/search?q=define:{word}'
    lang = lookup_lang()
    if lang:
        ans += f'#dobc={lang}'
    return ans


vprefs.defaults['lookup_locations'] = [
    {
        'name': 'Google dictionary',
        'url': 'https://www.google.com/search?q=define:{word}',
        'special_processor': 'google_dictionary',
        'langs': [],
    },

    {
        'name': 'Google search',
        'url':  'https://www.google.com/search?q={word}',
        'langs': [],
    },

    {
        'name': 'Wordnik',
        'url':  'https://www.wordnik.com/words/{word}',
        'langs': ['eng'],
    },

    {
        # Offline: renders our own JMdict entries into the webview instead of
        # loading a URL. See render_entries_html and update_query's kind branch.
        'name': 'JMdict (offline)',
        'kind': 'jmdict',
        'langs': ['jpn'],
    },
]
vprefs.defaults['lookup_location'] = 'Google dictionary'
vprefs.defaults['llm_lookup_tab_index'] = 0


class SourceEditor(Dialog):

    def __init__(self, parent, source_to_edit=None):
        self.all_names = {x['name'] for x in parent.all_entries}
        self.initial_name = self.initial_url = None
        self.langs = []
        self.initial_kind = 'web'
        if source_to_edit is not None:
            self.langs = source_to_edit.get('langs', [])
            self.initial_name = source_to_edit['name']
            self.initial_url = source_to_edit.get('url')
            self.initial_kind = source_to_edit.get('kind', 'web')
        Dialog.__init__(self, _('Edit lookup source'), 'viewer-edit-lookup-location', parent=parent)
        self.resize(self.sizeHint())

    def setup_ui(self):
        self.l = l = QFormLayout(self)
        self.name_edit = n = QLineEdit(self)
        n.setPlaceholderText(_('The name of the source'))
        n.setMinimumWidth(450)
        l.addRow(_('&Name:'), n)
        if self.initial_name:
            n.setText(self.initial_name)
            n.setReadOnly(True)
        self.kind_box = k = QComboBox(self)
        k.addItem(_('Web page (URL template)'), 'web')
        k.addItem(_('JMdict (offline dictionary)'), 'jmdict')
        k.setCurrentIndex(max(0, k.findData(self.initial_kind)))
        l.addRow(_('&Type:'), k)
        self.url_edit = u = QLineEdit(self)
        u.setPlaceholderText(_('The URL template of the source'))
        u.setMinimumWidth(n.minimumWidth())
        l.addRow(_('&URL:'), u)
        if self.initial_url:
            u.setText(self.initial_url)
        self.url_help = la = QLabel(_(
            'The URL template must starts with https:// and have {word} in it which will be replaced by the actual query'))
        la.setWordWrap(True)
        l.addRow(la)
        l.addRow(self.bb)
        k.currentIndexChanged.connect(self.update_url_enabled)
        self.update_url_enabled()
        if self.initial_name:
            u.setFocus(Qt.FocusReason.OtherFocusReason)

    def update_url_enabled(self):
        ''' The JMdict source renders locally, so it needs no URL. '''
        web = self.kind == 'web'
        self.url_edit.setEnabled(web)
        self.url_help.setEnabled(web)

    @property
    def kind(self):
        return self.kind_box.currentData()

    @property
    def source_name(self):
        return self.name_edit.text().strip()

    @property
    def url(self):
        return self.url_edit.text().strip()

    def accept(self):
        q = self.source_name
        if not q:
            return error_dialog(self, _('No name'), _(
                'You must specify a name'), show=True)
        if not self.initial_name and q in self.all_names:
            return error_dialog(self, _('Name already exists'), _(
                'A lookup source with the name {} already exists').format(q), show=True)
        if self.kind == 'web':
            if not self.url:
                return error_dialog(self, _('No URL'), _(
                    'You must specify a URL'), show=True)
            if not self.url.startswith('http://') and not self.url.startswith('https://'):
                return error_dialog(self, _('Invalid URL'), _(
                    'The URL must start with https://'), show=True)
            if '{word}' not in self.url:
                return error_dialog(self, _('Invalid URL'), _(
                    'The URL must contain the placeholder {word}'), show=True)
        return Dialog.accept(self)

    @property
    def entry(self):
        if self.kind == 'jmdict':
            return {'name': self.source_name, 'kind': 'jmdict', 'langs': self.langs}
        return {'name': self.source_name, 'url': self.url, 'langs': self.langs}


class SourcesEditor(Dialog):

    def __init__(self, parent, viewer=None):
        Dialog.__init__(self, _('Edit lookup sources'), 'viewer-edit-lookup-locations', parent=parent)

    def setup_ui(self):
        self.l = l = QVBoxLayout(self)
        self.la = la = QLabel(_('Double-click to edit an entry'))
        la.setWordWrap(True)
        l.addWidget(la)
        self.entries = e = QListWidget(self)
        e.setDragEnabled(True)
        e.itemDoubleClicked.connect(self.edit_source)
        e.viewport().setAcceptDrops(True)
        e.setDropIndicatorShown(True)
        e.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        e.setDefaultDropAction(Qt.DropAction.MoveAction)
        l.addWidget(e)
        l.addWidget(self.bb)
        self.build_entries(vprefs['lookup_locations'])

        self.add_button = b = self.bb.addButton(_('Add'), QDialogButtonBox.ButtonRole.ActionRole)
        b.setIcon(QIcon.ic('plus.png'))
        b.clicked.connect(self.add_source)
        self.remove_button = b = self.bb.addButton(_('Remove'), QDialogButtonBox.ButtonRole.ActionRole)
        b.setIcon(QIcon.ic('minus.png'))
        b.clicked.connect(self.remove_source)
        self.restore_defaults_button = b = self.bb.addButton(_('Restore defaults'), QDialogButtonBox.ButtonRole.ActionRole)
        b.clicked.connect(self.restore_defaults)

    def add_entry(self, entry, prepend=False):
        i = QListWidgetItem(entry['name'])
        i.setData(Qt.ItemDataRole.UserRole, entry.copy())
        self.entries.insertItem(0, i) if prepend else self.entries.addItem(i)

    def build_entries(self, entries):
        self.entries.clear()
        for entry in entries:
            self.add_entry(entry)

    def restore_defaults(self):
        self.build_entries(vprefs.defaults['lookup_locations'])

    def add_source(self):
        d = SourceEditor(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.add_entry(d.entry, prepend=True)

    def remove_source(self):
        idx = self.entries.currentRow()
        if idx > -1:
            self.entries.takeItem(idx)

    def edit_source(self, source_item):
        d = SourceEditor(self, source_item.data(Qt.ItemDataRole.UserRole))
        if d.exec() == QDialog.DialogCode.Accepted:
            source_item.setData(Qt.ItemDataRole.UserRole, d.entry)
            source_item.setData(Qt.ItemDataRole.DisplayRole, d.source_name)

    @property
    def all_entries(self):
        return [self.entries.item(r).data(Qt.ItemDataRole.UserRole) for r in range(self.entries.count())]

    def accept(self):
        entries = self.all_entries
        if not entries:
            return error_dialog(self, _('No sources'), _(
                'You must specify at least one lookup source'), show=True)
        if entries == vprefs.defaults['lookup_locations']:
            del vprefs['lookup_locations']
        else:
            vprefs['lookup_locations'] = entries
        return Dialog.accept(self)


def create_profile():
    ans = getattr(create_profile, 'ans', None)
    if ans is None:
        ans = QWebEngineProfile('viewer-lookup', QApplication.instance())
        ans.setHttpUserAgent(random_user_agent(allow_ie=False))
        setup_profile(ans)
        js = P('lookup.js', data=True, allow_user_override=True)
        insert_scripts(ans, create_script('lookup.js', js, injection_point=QWebEngineScript.InjectionPoint.DocumentCreation))
        s = ans.settings()
        s.setDefaultTextEncoding('utf-8')
        cs = ans.cookieStore()
        for c in google_consent_cookies():
            cookie = QNetworkCookie()
            cookie.setName(c['name'].encode())
            cookie.setValue(c['value'].encode())
            cookie.setDomain(c['domain'])
            cookie.setPath(c['path'])
            cookie.setSecure(False)
            cookie.setHttpOnly(False)
            cookie.setExpirationDate(QDateTime())
            cs.setCookie(cookie)
        create_profile.ans = ans
    return ans


class Page(QWebEnginePage):

    def javaScriptConsoleMessage(self, level, msg, linenumber, source_id):
        prefix = {
            QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: 'INFO',
            QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: 'WARNING'
        }.get(level, 'ERROR')
        if source_id == 'userscript:lookup.js':
            prints(f'{prefix}: {source_id}:{linenumber}: {msg}', file=sys.stderr)
            sys.stderr.flush()

    def zoom_in(self):
        factor = min(self.zoomFactor() + 0.2, 5)
        vprefs['lookup_zoom_factor'] = factor
        self.setZoomFactor(factor)

    def zoom_out(self):
        factor = max(0.25, self.zoomFactor() - 0.2)
        vprefs['lookup_zoom_factor'] = factor
        self.setZoomFactor(factor)

    def default_zoom(self):
        vprefs['lookup_zoom_factor'] = 1
        self.setZoomFactor(1)

    def set_initial_zoom_factor(self):
        try:
            self.setZoomFactor(float(vprefs.get('lookup_zoom_factor', 1)))
        except Exception:
            pass


class View(QWebEngineView):

    inspect_element = pyqtSignal()

    def contextMenuEvent(self, ev):
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction(_('Zoom in'), self.page().zoom_in)
        menu.addAction(_('Zoom out'), self.page().zoom_out)
        menu.addAction(_('Default zoom'), self.page().default_zoom)
        menu.addAction(_('Inspect'), self.do_inspect_element)
        menu.exec(ev.globalPos())

    def do_inspect_element(self):
        self.inspect_element.emit()


def set_sync_override(allowed):
    li = getattr(set_sync_override, 'instance', None)
    if li is not None:
        li.set_sync_override(allowed)


def blank_html():
    msg = _("Double click on a word in the book's text to look it up.")
    html = '<p>' + msg
    app = QApplication.instance()
    if app.is_dark_theme:
        pal = app.palette()
        bg = pal.color(QPalette.ColorRole.Base).name()
        fg = pal.color(QPalette.ColorRole.Text).name()
        html = f'<style> * {{ color: {fg}; background-color: {bg} }} </style>' + html
    return html


def _palette_colors():
    ''' (bg, fg, muted, accent) from the app palette, so JMdict HTML matches the
    viewer's light/dark theme (mirrors blank_html's approach). '''
    pal = QApplication.instance().palette()
    return (
        pal.color(QPalette.ColorRole.Base).name(),
        pal.color(QPalette.ColorRole.Text).name(),
        pal.color(QPalette.ColorRole.PlaceholderText).name(),
        pal.color(QPalette.ColorRole.Link).name(),
    )


def render_entries_html(candidates, query, max_entries=50):
    ''' Render JMdict ``lookup_candidates`` output as a read-only, theme-aware
    HTML page for the Lookup side panel (jisho-style: word 【reading】 + pos, an
    optional annotation line for deinflection/completion, then numbered senses).
    Text stays selectable -- that's default webview behavior. '''
    bg, fg, muted, accent = _palette_colors()
    esc = prepare_string_for_xml
    css = f'''
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 0.5rem 0.6rem; background: {bg}; color: {fg};
            font-family: sans-serif; line-height: 1.4; }}
    .msg {{ color: {muted}; }}
    .entry {{ padding: 0.55rem 0; border-bottom: 1px solid {muted}; }}
    .entry:last-child {{ border-bottom: none; }}
    .head {{ display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 0.45rem; }}
    .word {{ font-size: 1.3rem; font-weight: 600; }}
    .reading {{ color: {muted}; font-size: 1rem; }}
    .pos {{ color: {muted}; font-size: 0.8rem; margin-left: auto; }}
    .annot {{ color: {accent}; font-size: 0.85rem; margin: 0.2rem 0 0; }}
    ol.senses {{ margin: 0.3rem 0 0; padding-left: 1.5rem; }}
    ol.senses li {{ margin: 0.12rem 0; }}
    '''
    if not candidates:
        body = '<p class="msg">{}</p>'.format(
            esc(_('No JMdict entries for “{}”.').format(query)) if query
            else esc(_("Double click on a word in the book's text to look it up.")))
        return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{body}</body></html>'

    parts = []
    for c in candidates[:max_entries]:
        word = esc(c.get('word') or '')
        reading = c.get('reading') or ''
        reading_html = ''
        if reading and reading != c.get('word'):
            reading_html = f'<span class="reading">【{esc(reading)}】</span>'
        pos = c.get('pos') or []
        pos_html = f'<span class="pos">{esc(" · ".join(pos))}</span>' if pos else ''

        # Annotation: a completion says where it was completed from; otherwise a
        # deinflection shows its rule chain. (The completion's own rule name can
        # be an arbitrary member of an ambiguous set, so we don't surface it.)
        annot = ''
        if c.get('completed_from'):
            annot = _('completed from {}').format(c['completed_from'])
        elif c.get('reasons'):
            annot = ' '.join(c['reasons'])
        annot_html = f'<div class="annot">← {esc(annot)}</div>' if annot else ''

        senses = c.get('senses') or []
        if senses:
            lis = ''.join(f'<li>{esc("; ".join(s.get("glosses") or []))}</li>' for s in senses)
        else:  # fall back to the flattened gloss list
            lis = ''.join(f'<li>{esc(g)}</li>' for g in (c.get('glosses') or []))
        senses_html = f'<ol class="senses">{lis}</ol>' if lis else ''

        parts.append(
            f'<div class="entry"><div class="head">'
            f'<span class="word">{word}</span>{reading_html}{pos_html}</div>'
            f'{annot_html}{senses_html}</div>')

    body = ''.join(parts)
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{body}</body></html>'


class Lookup(QTabWidget):
    add_note_requested = pyqtSignal(str, str)

    def __init__(self, parent, viewer=None):
        QTabWidget.__init__(self, parent)
        self.viewer_parent = parent
        self.viewer = viewer
        self.setDocumentMode(True)
        self.setTabsClosable(False)

        self.is_visible = False
        self.selected_text = ''
        self.current_highlight_cache = None
        self.current_query = ''
        self.current_source = ''
        self._jmdict_db = None  # lazily-opened, UI-thread-only sqlite connection
        self.llm_panel = None
        self.llm_tab_index = -1
        self.current_book_metadata = {}

        self.debounce_timer = t = QTimer(self)
        t.setInterval(150), t.timeout.connect(self.update_query)

        self.dictionary_panel = self._create_dictionary_panel()
        self.addTab(self.dictionary_panel, QIcon.ic('dialog_question.png'), _('&Dictionary'))

        self.llm_container = QWidget(self)
        QVBoxLayout(self.llm_container).setContentsMargins(0, 0, 0, 0)
        self.llm_tab_index = self.addTab(self.llm_container, QIcon.ic('ai.png'), _('Ask &AI'))
        self.setTabVisible(self.llm_tab_index, not tweaks['hide_ai_features'])

        self.currentChanged.connect(self._tab_changed)
        set_sync_override.instance = self

    def book_loaded(self, book_data):
        self.current_book_metadata = book_data.get('metadata', {})
        if self.llm_panel:
            self.llm_panel.update_book_metadata(self.current_book_metadata)

    def _create_dictionary_panel(self):
        panel = QWidget(self)
        l = QVBoxLayout(panel)

        # Editable query field: the single source of truth for the lookup term.
        # A book selection writes into it (see selected_text_changed) and so does
        # the user; update_query always reads from here. This lets you fix up a
        # selection that the book split with punctuation before looking it up.
        self.query_edit = qe = QLineEdit(self)
        qe.setPlaceholderText(_('Type a word to look up, or select text in the book'))
        qe.setClearButtonEnabled(True)
        qe.textChanged.connect(self._on_query_edited)
        qe.returnPressed.connect(self.update_query)
        l.addWidget(qe)

        h = QHBoxLayout()
        l.addLayout(h)

        self.source_box = sb = QComboBox(self)
        self.label = la = QLabel(_('Lookup &in:'))
        h.addWidget(la), h.addWidget(sb), la.setBuddy(sb)
        self.view = View(self)
        self.view.inspect_element.connect(self.show_devtools)
        self._page = Page(create_profile(), self.view)
        apply_font_settings(self._page)
        secure_webengine(self._page, for_viewer=True)
        self.view.setPage(self._page)
        self._page.set_initial_zoom_factor()
        l.addWidget(self.view)
        self.populate_sources()
        self.source_box.currentIndexChanged.connect(self.source_changed)
        self.view.setHtml(blank_html())
        self.add_button = b = QPushButton(QIcon.ic('plus.png'), _('Add sources'))
        b.setToolTip(_('Add more sources at which to lookup words'))
        b.clicked.connect(self.add_sources)
        self.refresh_button = rb = QPushButton(QIcon.ic('view-refresh.png'), _('Refresh'))
        rb.setToolTip(_('Refresh the result to match the currently selected text'))
        rb.clicked.connect(self.refresh_clicked)

        h_bottom = QHBoxLayout()
        l.addLayout(h_bottom)
        h_bottom.addWidget(b)
        h_bottom.addWidget(rb)

        self.auto_update_query = a = QCheckBox(_('Update on selection change'), self)
        self.disallow_auto_update = False
        a.setToolTip(textwrap.fill(
            _('Automatically update the displayed result when selected text in the book changes. With this disabled'
              ' the lookup is changed only when clicking the Refresh button.')))
        a.setChecked(vprefs['auto_update_lookup'])
        a.stateChanged.connect(self.auto_update_state_changed)
        l.addWidget(a)
        self.update_refresh_button_status()
        return panel

    def _activate_llm_panel(self):
        ' Only load LLM code when actually requested by the user '
        if self.llm_panel is not None:
            return
        from calibre.live import start_worker
        start_worker()  # needed for live loading of AI backends
        from calibre.gui2.viewer.llm import LLMPanel
        self.llm_panel = LLMPanel(self)
        self.llm_container.layout().addWidget(self.llm_panel)
        if self.current_book_metadata:
            self.llm_panel.update_book_metadata(self.current_book_metadata)
        self.llm_panel.add_note_requested.connect(self.add_note_requested)
        self.llm_panel.update_with_text(self.selected_text)

    def _tab_changed(self, index):
        vprefs.set('llm_lookup_tab_index', index)
        if index == self.llm_tab_index:
            self._activate_llm_panel()
        self.update_query()

    def set_sync_override(self, allowed):
        self.disallow_auto_update = not allowed
        if self.auto_update_query.isChecked() and allowed:
            self.update_query()

    def auto_update_state_changed(self, state):
        vprefs['auto_update_lookup'] = self.auto_update_query.isChecked()
        self.update_refresh_button_status()

    def show_devtools(self):
        if not hasattr(self, '_devtools_page'):
            self._devtools_page = QWebEnginePage()
            self._devtools_view = QWebEngineView(self)
            self._devtools_view.setPage(self._devtools_page)
            setup_profile(self._devtools_page.profile())
            self._page.setDevToolsPage(self._devtools_page)
            self._devtools_dialog = d = QDialog(self)
            d.setWindowTitle('Inspect Lookup page')
            v = QVBoxLayout(d)
            v.addWidget(self._devtools_view)
            d.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            d.bb.rejected.connect(d.reject)
            v.addWidget(d.bb)
            d.resize(QSize(800, 600))
            d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._devtools_dialog.show()
        self._page.triggerAction(QWebEnginePage.WebAction.InspectElement)

    def add_sources(self):
        if SourcesEditor(self).exec() == QDialog.DialogCode.Accepted:
            self.populate_sources()
            self.source_box.setCurrentIndex(0)
            self.update_query()

    def source_changed(self):
        s = self.source
        if s is not None:
            vprefs['lookup_location'] = s['name']
            self.update_query()

    def populate_sources(self):
        sb = self.source_box
        sb.clear()
        sb.blockSignals(True)
        for item in vprefs['lookup_locations']:
            sb.addItem(item['name'], item)
        idx = sb.findText(vprefs['lookup_location'], Qt.MatchFlag.MatchExactly)
        if idx > -1:
            sb.setCurrentIndex(idx)
        sb.blockSignals(False)

    def visibility_changed(self, is_visible):
        self.is_visible = is_visible
        if is_visible:
            last_idx = vprefs.get('llm_lookup_tab_index', 0)
            if last_idx == self.llm_tab_index and tweaks['hide_ai_features']:
                last_idx = 0
            if 0 <= last_idx < self.count():
                self.setCurrentIndex(last_idx)
            if self.llm_panel:
                self.llm_panel.update_book_metadata(self.current_book_metadata)
        self.update_query()

    @property
    def source(self):
        idx = self.source_box.currentIndex()
        if idx > -1:
            return self.source_box.itemData(idx)

    @property
    def url_template(self):
        idx = self.source_box.currentIndex()
        if idx > -1:
            return self.source_box.itemData(idx).get('url')

    @property
    def source_kind(self):
        s = self.source
        return (s or {}).get('kind', 'web')

    @property
    def source_id(self):
        ''' Identity used to detect when the shown result is stale. For web
        sources that is the URL template; the JMdict source has no URL, so key
        it by name. '''
        s = self.source
        if not s:
            return None
        if s.get('kind') == 'jmdict':
            return 'jmdict:' + s['name']
        return s.get('url')

    @property
    def special_processor(self):
        idx = self.source_box.currentIndex()
        if idx > -1:
            return special_processors.get(self.source_box.itemData(idx).get('special_processor'))

    @property
    def query_text(self):
        ' The effective lookup term: whatever is in the editable query field. '
        return self.query_edit.text().strip()

    def update_refresh_button_status(self):
        # The query field drives lookups live (debounced), so Refresh only has a
        # job when auto-update is off: pull the current book selection into the
        # field. Enable it when there is a selection not already shown there.
        b = self.refresh_button
        b.setVisible(not self.auto_update_query.isChecked())
        b.setEnabled(bool(self.selected_text) and self.selected_text != self.query_text)

    def _on_query_edited(self):
        ' The query field changed (user typing or a programmatic selection push). '
        self.update_refresh_button_status()
        self.debounce_timer.start()

    def refresh_clicked(self):
        ' Pull the current selection into the field, then look it up. '
        if self.selected_text:
            self.query_edit.setText(self.selected_text)
        self.update_query()

    def update_query(self):
        self.debounce_timer.stop()
        if not self.is_visible:
            return

        current_idx = self.currentIndex()
        if current_idx == self.llm_tab_index:
            if self.llm_panel:
                self.llm_panel.update_with_text(self.selected_text)
            return

        query = self.query_text
        if not query:
            self.update_refresh_button_status()
            return
        if self.current_query == query and self.current_source == self.source_id:
            return
        self.current_source = self.source_id
        if self.source_kind == 'jmdict':
            self.view.setHtml(self._jmdict_html(query))
        else:
            sp = self.special_processor
            if sp is None:
                url = self.url_template.format(word=query)
            else:
                url = sp(query)
            self.view.load(QUrl(url))
        self.current_query = query
        self.update_refresh_button_status()

    def _jmdict_html(self, query):
        ''' Look ``query`` up in the bundled offline JMdict and render it as HTML
        for the webview. Runs on the GUI thread (called from update_query), which
        is the only thread that touches the sqlite connection. '''
        from calibre.gui2.viewer.anki_cards.jmdict import JMdict, JMdictUnavailable
        from calibre.gui2.viewer.anki_cards.lookup import lookup_candidates
        try:
            if self._jmdict_db is None:
                self._jmdict_db = JMdict()
            candidates = lookup_candidates(query, db=self._jmdict_db)
        except JMdictUnavailable:
            bg, fg, muted, accent = _palette_colors()
            return (f'<body style="background:{bg};color:{fg};font-family:sans-serif;padding:0.6rem">'
                    f'<p>{prepare_string_for_xml(_("The offline JMdict database is not available."))}</p></body>')
        except Exception:
            import traceback
            traceback.print_exc()
            candidates = []
        return render_entries_html(candidates, query)

    def _find_highlight_by_uuid(self, uuid):
        if not uuid or not self.viewer:
            return None
        with suppress(Exception):
            highlight_list = self.viewer.current_book_data['annotations_map']['highlight']
            for h in highlight_list:
                if h.get('uuid') == uuid:
                    return h
        return None

    def selected_text_changed(self, text, annot_data):
        text = text.replace('\u00ad', '')  # remove soft hyphens
        processed_annot_data = None
        uuid_from_signal = None

        if isinstance(annot_data, dict):
            processed_annot_data = annot_data
            uuid_from_signal = processed_annot_data.get('uuid')
        elif isinstance(annot_data, str):
            try:
                data = json.loads(annot_data)
                if isinstance(data, dict):
                    processed_annot_data = data
                    uuid_from_signal = processed_annot_data.get('uuid')
            except (json.JSONDecodeError, TypeError):
                uuid_from_signal = annot_data

        if uuid_from_signal and not processed_annot_data:
            processed_annot_data = self._find_highlight_by_uuid(uuid_from_signal)

        if not processed_annot_data and text and self.current_highlight_cache:
            if self.current_highlight_cache.get('text', '').strip() == text.strip():
                processed_annot_data = self.current_highlight_cache

        if processed_annot_data and processed_annot_data.get('uuid'):
            self.current_highlight_cache = processed_annot_data

        self.selected_text = text or ''

        if self.selected_text and self.currentIndex() == self.llm_tab_index:
            self.viewer_parent.web_view.generic_action('suppress-selection-popup', True)

        # When auto-update is on, feed the selection into the query field; its
        # textChanged handler debounces the actual lookup. When off, we leave the
        # field alone so a hand-edited query survives new selections (Refresh
        # pulls the selection in on demand).
        if self.selected_text and not self.disallow_auto_update and self.auto_update_query.isChecked():
            self.query_edit.setText(self.selected_text)

        self.update_refresh_button_status()
        if self.llm_panel:
            self.llm_panel.update_with_text(self.selected_text)

    def on_forced_show(self):
        self.update_query()
