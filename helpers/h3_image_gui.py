"""Portable MiniMax H3 Image GUI — JSON persistence; no QSettings."""
import sys, json, os, time, random, threading
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTabWidget, QLabel, QLineEdit, QPushButton, QPlainTextEdit, QScrollArea,
    QComboBox, QSpinBox, QCheckBox, QFileDialog, QMessageBox, QListWidget, QGroupBox,
    QListWidgetItem, QDialog, QAbstractItemView, QSizePolicy
)
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QUrl, QSize
from PySide6.QtGui import QPixmap, QDesktopServices, QIcon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.h3_image.engine import Server, generate, installed_models, ensure_backend, H3Error

CFG = ROOT / 'presets' / 'setsave' / 'h3_image_settings.json'
DEFAULT = {
    'backend_url': 'http://127.0.0.1:8197',
    'backend_python': str(ROOT / 'environments' / 'h3_image_comfy' / 'Scripts' / 'python.exe'),
    'backend_main': str(ROOT / 'models' / 'minimax_h3_image' / 'comfyui_backend' / 'main.py'),
    'diffusion': '', 'encoder': '', 'vae': '',
    'last_checkpoint_dir': str(ROOT / 'models'),
    'output_dir': str(ROOT / 'output' / 'images'),
    'edit_output_dir': str(ROOT / 'output' / 'edits'),
    'filename_prefix': 'H3_Image',
    'last_image_dir': str(ROOT),
    'prompt': '', 'mode': 'Generate',
    'width': 2048, 'height': 2048,
    'seed': -1, 'steps': 6,
    'enhanced_details': False, 'detail_influence': 0.25,
    'lowvram': True,
    'smart_framing': True,
    'references': [],
    'reuse_h3_encoder': True,
}


def read_settings():
    try:
        obj = json.loads(CFG.read_text(encoding='utf-8'))
        merged = {**DEFAULT, **{k: v for k, v in obj.items() if k in DEFAULT}}
        new_backend = ROOT / 'models' / 'minimax_h3_image' / 'comfyui_backend' / 'main.py'
        old_backend = ROOT / 'vendor' / 'ComfyUI' / 'main.py'
        current = Path(str(merged.get('backend_main', '')) )
        if current == old_backend and not current.exists() and new_backend.exists():
            merged['backend_main'] = str(new_backend)
        elif not merged.get('backend_main'):
            merged['backend_main'] = str(new_backend)
        return merged
    except Exception:
        return dict(DEFAULT)


def embedded_settings(settings):
    """Migrate standard standalone paths; keep intentional custom locations."""
    d = dict(settings)
    old_python = Path(d.get('backend_python') or '')
    old_root = None
    if len(old_python.parents) >= 4 and old_python.parent.name.lower() == 'scripts' and old_python.parent.parent.name.lower() == 'h3_image_comfy' and old_python.parent.parent.parent.name.lower() == 'environments':
        old_root = old_python.parents[3]
    if old_root is not None and old_root != ROOT:
        for key, leaf in (('output_dir','images'), ('edit_output_dir','edits')):
            if Path(d.get(key) or '') == old_root / 'output' / leaf:
                d[key] = DEFAULT[key]
        for key in ('diffusion', 'encoder', 'vae'):
            if not d.get(key): continue
            try:
                migrated = ROOT / Path(d[key]).relative_to(old_root)
                if migrated.is_file(): d[key] = str(migrated)
            except ValueError:
                pass
    for key in ('backend_python', 'backend_main', 'backend_url'):
        d[key] = DEFAULT[key]
    for key in ('output_dir', 'edit_output_dir'):
        if not d.get(key): d[key] = DEFAULT[key]
    return d


def write_settings(obj):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CFG.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, CFG)


class Combo(QComboBox):
    def wheelEvent(self, e):
        if not self.hasFocus():
            e.ignore()
            return
        super().wheelEvent(e)


class Spin(QSpinBox):
    def wheelEvent(self, e):
        if not self.hasFocus():
            e.ignore()
            return
        super().wheelEvent(e)


class Signals(QObject):
    log = Signal(str)
    done = Signal(object, object)


class ImageRefList(QListWidget):
    filesDropped = Signal(list)
    reordered = Signal()
    itemOpenRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Snap)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setSpacing(8)
        self.setIconSize(QSize(88, 88))
        self.setMinimumHeight(132)
        self.setMaximumHeight(150)
        self.itemDoubleClicked.connect(self._open_item)

    def _open_item(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self.itemOpenRequested.emit(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
            if paths:
                self.filesDropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)
        self.reordered.emit()


class ImagePreviewDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(920, 760)
        v = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        pix = QPixmap(path)
        if pix.isNull():
            label.setText(path)
        else:
            label.setPixmap(pix)
        scroll.setWidget(label)
        v.addWidget(scroll)
        foot = QHBoxLayout()
        open_btn = QPushButton('Open in folder')
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve().parent))))
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.accept)
        foot.addStretch()
        foot.addWidget(open_btn)
        foot.addWidget(close_btn)
        v.addLayout(foot)


class GUI(QMainWindow):
    def __init__(self, owner=None):
        super().__init__(owner)
        self.owner = owner
        if owner is not None:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.d = read_settings()
        if owner is not None:
            self.d = embedded_settings(self.d)
        self.last_saved_state = None
        self.scanning_models = False
        self.suspend_saves = True
        self.missing_models = []
        self.sig = Signals()
        self.sig.log.connect(self.log)
        self.sig.done.connect(self.finished)
        self.server = Server(self.sig.log.emit)
        self.stop_event = threading.Event()
        self.busy = False
        self.loading = True
        self.job_started = None
        self.current_job_desc = ''
        self.setWindowTitle('MiniMax H3 Image — GrizzlyMax')
        if owner is None:
            self.resize(1180, 830)
        else:
            self.setMinimumSize(0, 0)
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setup_ui()
        self.connect_change_tracking()
        self.restore_ui()
        self.loading = False
        self.suspend_saves = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        QTimer.singleShot(250, self.scan_models)

    def hbox(self, *widgets):
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        for item in widgets:
            l.addWidget(item)
        return w

    def scroll(self, w):
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        w.setMinimumWidth(0)
        w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        s.setWidget(w)
        return s

    def edit_path(self, name, folder=False):
        e = QLineEdit()
        b = QPushButton('Browse…')

        def pick():
            initial = e.text() or self.d.get('last_image_dir', str(ROOT))
            if folder:
                p = QFileDialog.getExistingDirectory(self, 'Select folder', initial)
            else:
                p = QFileDialog.getOpenFileName(self, 'Select file', initial, 'All files (*)')[0]
            if p:
                e.setText(p)
                self.schedule_save()

        b.clicked.connect(pick)
        setattr(self, name, e)
        return self.hbox(e, b)

    def setup_ui(self):
        tabs = QTabWidget()
        if self.owner is not None:
            tabs.setUsesScrollButtons(True)
            tabs.setMinimumWidth(0)
            tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(tabs)

        # Main tab
        home = QWidget()
        v = QVBoxLayout(home)
        self.mode = Combo()
        self.mode.addItems(['Generate', 'Edit'])
        self.mode.currentTextChanged.connect(self.mode_update)
        self.status = QLabel('Ready')
        self.status.setWordWrap(True)
        v.addWidget(self.hbox(QLabel('Mode'), self.mode, self.status))

        v.addWidget(QLabel('Prompt / editing instructions'))
        self.prompt = QPlainTextEdit()
        self.prompt.setMinimumHeight(130)
        self.prompt.setPlaceholderText('Describe the image. In Edit mode, the first reference is the image to edit.')
        v.addWidget(self.prompt, 2)

        self.ref_label = QLabel('Reference images (0/16)')
        add_btn = QPushButton('Add images')
        add_btn.clicked.connect(self.add_images)
        self.remove_btn = QPushButton('Remove selected')
        self.remove_btn.clicked.connect(self.remove_selected_image)
        clear_btn = QPushButton('Clear')
        clear_btn.clicked.connect(self.clear_images)
        self.ref_toolbar = QWidget()
        ref_layout = QVBoxLayout(self.ref_toolbar)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        self.ref_label.setWordWrap(True)
        ref_layout.addWidget(self.ref_label)
        ref_layout.addWidget(self.hbox(add_btn, self.remove_btn, clear_btn))
        v.addWidget(self.ref_toolbar)

        self.images = ImageRefList()
        self.images.filesDropped.connect(self.add_image_paths)
        self.images.reordered.connect(self.on_refs_changed)
        self.images.itemOpenRequested.connect(self.show_image_preview)
        self.images.currentRowChanged.connect(self.update_ref_controls)
        v.addWidget(self.images)
        hint = QLabel('Drag files in, drag thumbnails to reorder, double-click a thumbnail to enlarge. In Edit mode, reference 1 is the edit target.')
        hint.setWordWrap(True)
        self.ref_hint = hint
        v.addWidget(self.ref_hint)

        self.presets = {
            '1024 × 1024 Draft': (1024, 1024),
            '1280 × 720 Draft 16:9': (1280, 720),
            '1360 × 768 Draft 16:9': (1360, 768),
            '1536 × 864 Draft 16:9': (1536, 864),
            '1536 × 1024 Draft 3:2': (1536, 1024),
            '1536 × 1152 4:3': (1536, 1152),
            '1920 × 1080 HD 16:9': (1920, 1080),
            '2048 × 1152 16:9': (2048, 1152),
            '2048 × 1536 4:3': (2048, 1536),
            '2048 × 2048 Square': (2048, 2048),
            '2304 × 1296 16:9': (2304, 1296),
            '2304 × 1728 4:3': (2304, 1728),
            '2736 × 1536 16:9': (2736, 1536),
            '3072 × 1728 16:9': (3072, 1728),
            '3072 × 3072 Square': (3072, 3072),
            '4096 × 2304 16:9': (4096, 2304),
            '4096 × 3072 4:3': (4096, 3072),
            '4096 × 4096 Square': (4096, 4096),
            '864 × 1536 Portrait 9:16': (864, 1536),
            '1152 × 2048 Portrait 9:16': (1152, 2048),
            '1296 × 2304 Portrait 9:16': (1296, 2304),
            '1536 × 2736 Portrait 9:16': (1536, 2736),
            'Custom': None,
        }
        self.preset = Combo()
        self.preset.addItems(self.presets)
        self.preset.currentTextChanged.connect(self.preset_changed)
        self.w = Spin(); self.h = Spin()
        for x in (self.w, self.h):
            x.setRange(256, 8192)
            x.setSingleStep(32)
            x.valueChanged.connect(self.update_preset_selection)
        self.seed = QLineEdit()
        self.seed.setPlaceholderText('Random')
        self.seed.setMaximumWidth(140)
        self.steps = Spin()
        self.steps.setRange(1, 100)
        self.steps.setValue(6)
        self.steps.setToolTip('Sampling steps. Default: 6. Higher values are experimental.')
        if self.owner is not None:
            v.addWidget(self.hbox(QLabel('Size'), self.preset))
            v.addWidget(self.hbox(QLabel('W'), self.w, QLabel('H'), self.h))
            v.addWidget(self.hbox(QLabel('Seed'), self.seed, QLabel('Steps'), self.steps))
        else:
            v.addWidget(self.hbox(QLabel('Size'), self.preset, QLabel('W'), self.w, QLabel('H'), self.h, QLabel('Seed'), self.seed, QLabel('Steps'), self.steps))
        self.enhanced_details = QCheckBox('Enhanced fine details')
        self.enhanced_details.setToolTip('Experimental: adds empty-image vision conditioning early in sampling. May make portraits waxy.')
        self.detail_influence = Spin()
        self.detail_influence.setRange(1, 100)
        self.detail_influence.setValue(25)
        self.detail_influence.setSuffix('%')
        self.detail_influence.setToolTip('Conditioning schedule duration, not enhancement strength. Default: 25%.')
        self.detail_influence.setEnabled(False)
        self.enhanced_details.toggled.connect(self.update_detail_controls)
        v.addWidget(self.hbox(self.enhanced_details, QLabel('Influence'), self.detail_influence))
        v.addStretch(1)

        self.go = QPushButton('Generate image')
        self.go.setMinimumHeight(42)
        self.go.clicked.connect(self.generate)
        self.cancel = QPushButton('Stop waiting')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_wait)
        open_folder = QPushButton('Open output')
        open_folder.clicked.connect(self.open_output)
        action_row = self.hbox(self.go, self.cancel, open_folder)
        v.addWidget(action_row)
        if self.owner is not None: action_row.hide()

        self.preview = QLabel('Image preview')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(190)
        self.preview.setStyleSheet('background-color:#23272b;color:#dddddd;border:1px solid #555;')
        v.addWidget(self.preview, 3)
        if self.owner is not None: self.preview.hide()
        tabs.addTab(self.scroll(home), 'Generate / Edit')

        # Settings tab
        setup = QWidget()
        s = QVBoxLayout(setup)
        group = QGroupBox('Model checkpoints')
        form = QFormLayout(group)
        if self.owner is not None: form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for key, title in [('diffusion', 'Diffusion checkpoint'), ('encoder', 'Text encoder'), ('vae', 'Image VAE')]:
            combo = Combo()
            combo.setEditable(False)
            combo.setInsertPolicy(QComboBox.NoInsert)
            if self.owner is not None:
                combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(12)
                combo.setMinimumWidth(0)
                combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            setattr(self, key, combo)
            browse = QPushButton('Browse…')
            browse.clicked.connect(lambda checked=False, k=key: self.browse_checkpoint(k))
            if key == 'encoder': self.encoder_browse = browse
            form.addRow(title, self.hbox(combo, browse))
            if key == 'encoder':
                self.reuse_encoder = QCheckBox('Use MiniMax H3 text encoder (recommended)')
                self.reuse_encoder.setVisible(self.owner is not None)
                form.addRow('', self.reuse_encoder)
        self.model_status = QLabel('')
        self.model_status.setWordWrap(True)
        refresh = QPushButton('Refresh checkpoints')
        refresh.clicked.connect(self.scan_models)
        form.addRow('', refresh)
        form.addRow('', self.model_status)
        s.addWidget(group)

        advanced = QGroupBox('Performance and composition')
        af = QFormLayout(advanced)
        if self.owner is not None: af.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.lowvram = QCheckBox('Low VRAM mode (slower, less memory)')
        self.smart_framing = QCheckBox('Smart reference framing' if self.owner is not None else 'Expand references toward output shape (helps 1:1 → 16:9)')
        self.smart_framing.setToolTip('Expand references toward output shape to reduce cropping.')
        af.addRow('', self.lowvram)
        af.addRow('', self.smart_framing)
        s.addWidget(advanced)

        outputs = QGroupBox('Outputs')
        of = QFormLayout(outputs)
        if self.owner is not None: of.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        of.addRow('Generated images', self.edit_path('output_dir', folder=True))
        of.addRow('Edited images', self.edit_path('edit_output_dir', folder=True))
        self.prefix = QLineEdit()
        of.addRow('Default filename', self.prefix)
        s.addWidget(outputs)
        s.addStretch()
        tabs.addTab(self.scroll(setup), 'Settings')

        # Logs tab
        logtab = QWidget()
        lv = QVBoxLayout(logtab)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        lv.addWidget(self.logs)
        reset = QPushButton('Clear displayed logs')
        reset.clicked.connect(self.logs.clear)
        lv.addWidget(reset)
        tabs.addTab(logtab, 'Logs')

    def connect_change_tracking(self):
        self.mode.currentTextChanged.connect(self.schedule_save)
        self.reuse_encoder.toggled.connect(self.schedule_save)
        self.reuse_encoder.toggled.connect(self._sync_encoder_mode)
        self.prompt.textChanged.connect(self.schedule_save)
        self.preset.currentTextChanged.connect(self.schedule_save)
        self.w.valueChanged.connect(self.schedule_save)
        self.h.valueChanged.connect(self.schedule_save)
        self.seed.textChanged.connect(self.schedule_save)
        self.steps.valueChanged.connect(self.schedule_save)
        self.enhanced_details.toggled.connect(self.schedule_save)
        self.detail_influence.valueChanged.connect(self.schedule_save)
        self.output_dir.textChanged.connect(self.schedule_save)
        self.edit_output_dir.textChanged.connect(self.schedule_save)
        self.prefix.textChanged.connect(self.schedule_save)
        self.lowvram.toggled.connect(self.schedule_save)
        self.smart_framing.toggled.connect(self.schedule_save)
        for key in ('diffusion', 'encoder', 'vae'):
            getattr(self, key).currentIndexChanged.connect(self.schedule_save)
            getattr(self, key).currentIndexChanged.connect(self.model_selection_changed)

    def restore_ui(self):
        d = self.d
        self.mode.setCurrentText(d['mode'])
        self.prompt.setPlainText(d['prompt'])
        self.w.setValue(d['width'])
        self.h.setValue(d['height'])
        self.seed.setText('' if d['seed'] < 0 else str(d['seed']))
        try:
            self.steps.setValue(int(d.get('steps', 6)))
        except (TypeError, ValueError, OverflowError):
            self.steps.setValue(6)
        self.enhanced_details.setChecked(bool(d.get('enhanced_details', False)))
        try:
            self.detail_influence.setValue(round(float(d.get('detail_influence', 0.25)) * 100))
        except (TypeError, ValueError, OverflowError):
            self.detail_influence.setValue(25)
        self.add_image_paths([x for x in d['references'] if Path(x).exists()], save=False)
        self.output_dir.setText(d['output_dir'])
        self.edit_output_dir.setText(d['edit_output_dir'])
        self.prefix.setText(d['filename_prefix'])
        self.reuse_encoder.setChecked(bool(d.get('reuse_h3_encoder', True)) and self.owner is not None)
        self._sync_encoder_mode(self.reuse_encoder.isChecked())
        self.lowvram.setChecked(d['lowvram'])
        self.smart_framing.setChecked(d.get('smart_framing', True))
        self.update_preset_selection()
        self.mode_update(self.mode.currentText())
        self.update_ref_controls()

    def gather(self):
        d = self.d.copy()
        d.update({
            'mode': self.mode.currentText(),
            'reuse_h3_encoder': self.reuse_encoder.isChecked() if self.owner is not None else bool(self.d.get('reuse_h3_encoder', True)),
            'prompt': self.prompt.toPlainText(),
            'width': self.w.value(),
            'height': self.h.value(),
            'seed': int(self.seed.text()) if self.seed.text().isdigit() else -1,
            'steps': self.steps.value(),
            'enhanced_details': self.enhanced_details.isChecked(),
            'detail_influence': self.detail_influence.value() / 100.0,
            'references': [self.images.item(i).data(Qt.UserRole) for i in range(self.images.count())],
            'lowvram': self.lowvram.isChecked(),
            'smart_framing': self.smart_framing.isChecked(),
            'filename_prefix': self.prefix.text(),
            'output_dir': self.output_dir.text().strip(),
            'edit_output_dir': self.edit_output_dir.text().strip(),
            'diffusion': self.diffusion.currentData() or self.d.get('diffusion', ''),
            'encoder': self.encoder.currentData() or self.d.get('encoder', ''),
            'vae': self.vae.currentData() or self.d.get('vae', ''),
        })
        return d

    def schedule_save(self, *args):
        if self.loading or self.suspend_saves or self.scanning_models:
            return
        self.save_timer.start(400)

    def save(self):
        if self.loading or self.suspend_saves or self.scanning_models:
            return
        try:
            current = self.gather()
            if current == self.last_saved_state:
                return
            self.d = current
            write_settings(current)
            self.last_saved_state = json.loads(json.dumps(current))
        except Exception as e:
            self.log('Settings save failed: ' + str(e))

    def log(self, message):
        self.logs.appendPlainText(f'[{time.strftime("%H:%M:%S")}] {message}')
        p = ROOT / 'logs' / f'h3_image_gui_{time.strftime("%Y%m%d")}.log'
        p.parent.mkdir(exist_ok=True)
        with p.open('a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {message}\n')

    def update_detail_controls(self, *args):
        allowed = self.mode.currentText() == 'Generate'
        self.enhanced_details.setEnabled(allowed)
        self.detail_influence.setEnabled(allowed and self.enhanced_details.isChecked())

    def mode_update(self, mode):
        self.update_detail_controls()
        self.go.setText('Edit image' if mode == 'Edit' else 'Generate image')
        if self.owner is not None and hasattr(self.owner, 'gen'):
            self.owner._sync_main_generate_button()
        for widget in (self.ref_toolbar, self.images, self.ref_hint):
            widget.setVisible(mode == 'Edit')
        self.update_thumbnail_labels()
        self.update_ref_label()

    def preset_changed(self, key):
        value = self.presets[key]
        if value:
            self.w.setValue(value[0])
            self.h.setValue(value[1])

    def update_preset_selection(self):
        current = (self.w.value(), self.h.value())
        self.preset.blockSignals(True)
        match = next((name for name, val in self.presets.items() if val == current), 'Custom')
        self.preset.setCurrentText(match)
        self.preset.blockSignals(False)

    def make_thumb_icon(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            return QIcon()
        scaled = pix.scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QIcon(scaled)

    def update_ref_label(self):
        extra = ' — ref 1 is the image to edit' if self.mode.currentText() == 'Edit' and self.images.count() else ''
        self.ref_label.setText(f'Reference images ({self.images.count()}/16){extra}')

    def update_ref_controls(self):
        self.remove_btn.setEnabled(self.images.currentItem() is not None)

    def update_thumbnail_labels(self):
        edit_mode = self.mode.currentText() == 'Edit'
        for i in range(self.images.count()):
            item = self.images.item(i)
            path = item.data(Qt.UserRole)
            base = Path(path).name
            if edit_mode and i == 0:
                item.setText(f'1. Edit target\n{base}')
            else:
                item.setText(f'{i + 1}. {base}')
            item.setToolTip(path)
        self.update_ref_label()
        self.update_ref_controls()

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Choose reference images', self.d['last_image_dir'], 'Images (*.png *.jpg *.jpeg *.webp *.bmp)')
        self.add_image_paths(files)

    def add_image_paths(self, files, save=True):
        if not files:
            return
        existing = {self.images.item(i).data(Qt.UserRole) for i in range(self.images.count())}
        added = 0
        for path in files:
            if self.images.count() >= 16:
                break
            if not path or path in existing or not Path(path).is_file():
                continue
            item = QListWidgetItem(self.make_thumb_icon(path), '')
            item.setData(Qt.UserRole, path)
            item.setTextAlignment(Qt.AlignCenter)
            item.setSizeHint(QSize(108, 108))
            self.images.addItem(item)
            existing.add(path)
            added += 1
        if files:
            self.d['last_image_dir'] = str(Path(files[-1]).parent)
        if added:
            self.update_thumbnail_labels()
            if save:
                self.on_refs_changed()

    def remove_selected_image(self):
        row = self.images.currentRow()
        if row >= 0:
            self.images.takeItem(row)
            self.on_refs_changed()

    def clear_images(self):
        self.images.clear()
        self.on_refs_changed()

    def on_refs_changed(self):
        self.update_thumbnail_labels()
        self.schedule_save()

    def show_image_preview(self, path):
        dlg = ImagePreviewDialog(path, self)
        dlg.exec()

    def browse_checkpoint(self, key):
        if self.busy:
            return
        combo = getattr(self, key)
        current = combo.currentData()
        initial = str(Path(current).parent) if current else self.d['last_checkpoint_dir']
        path, _ = QFileDialog.getOpenFileName(self, 'Select ' + key + ' checkpoint', initial, 'Safetensors (*.safetensors)')
        if not path or not Path(path).is_file():
            return
        path = str(Path(path).resolve())
        self.d['last_checkpoint_dir'] = str(Path(path).parent)
        index = combo.findData(path)
        if index < 0:
            combo.addItem(Path(path).name, path)
            index = combo.count() - 1
        combo.setItemData(index, path, Qt.ToolTipRole)
        combo.setCurrentIndex(index)
        self.model_selection_changed()
        self.save()

    def reload_install_settings(self):
        self.save_timer.stop()
        fresh = embedded_settings(read_settings())
        self.scanning_models = True
        try:
            for key in ('diffusion', 'encoder', 'vae', 'reuse_h3_encoder', 'backend_python', 'backend_main', 'backend_url'):
                self.d[key] = fresh[key]
            for key in ('diffusion', 'encoder', 'vae'):
                getattr(self, key).clear()
            self.reuse_encoder.setChecked(bool(fresh['reuse_h3_encoder']))
        finally:
            self.scanning_models = False
        self.scan_models()

    def scan_models(self):
        if self.busy:
            return
        self.scanning_models = True
        try:
            files = installed_models()
            self.missing_models = []
            for key in ('diffusion', 'encoder', 'vae'):
                combo = getattr(self, key)
                desired = combo.currentData() or self.d.get(key, '')
                paths = list(files.get(key, []))
                if desired and Path(desired).is_file():
                    desired = str(Path(desired).resolve())
                    if desired not in paths:
                        paths.append(desired)
                combo.blockSignals(True)
                try:
                    combo.clear()
                    for path in paths:
                        combo.addItem(Path(path).name, path)
                        combo.setItemData(combo.count()-1, path, Qt.ToolTipRole)
                    match = combo.findData(desired)
                    if match < 0 and desired and not Path(desired).is_absolute():
                        match = combo.findText(desired)
                    if desired and match < 0:
                        if self.owner is not None and paths and not Path(desired).is_file() and not str(desired).startswith(str(ROOT)):
                            match = next((i for i, path in enumerate(paths) if Path(path).name == Path(desired).name), -1)
                            if match < 0: self.missing_models.append((key, desired))
                        else:
                            self.missing_models.append((key, desired))
                    combo.setCurrentIndex(match if desired else (0 if paths else -1))
                finally:
                    combo.blockSignals(False)
            self.update_model_status({key: range(getattr(self, key).count()) for key in files})
        finally:
            self.scanning_models = False
        self.save()

    def model_selection_changed(self, *args):
        if self.loading or self.scanning_models:
            return
        self.missing_models = [(key, name) for key, name in self.missing_models
                               if not getattr(self, key).currentText()]
        self.update_model_status({key: range(getattr(self, key).count())
                                  for key in ('diffusion', 'encoder', 'vae')})

    def update_model_status(self, files):
        shared = self.owner is not None and self.reuse_encoder.isChecked()
        missing_groups = [key for key, items in files.items() if not items and not (shared and key == 'encoder')]
        lines = []
        if missing_groups:
            lines.append('Missing model files for: ' + ', '.join(missing_groups))
        missing = [(key, name) for key, name in self.missing_models if not (shared and key == 'encoder')]
        if missing:
            lines.append('Saved selection not found: ' + '; '.join(f'{key}={name}' for key, name in missing))
            self.log(lines[-1])
        text = ' | '.join(lines)
        if shared and not lines: text = 'Using the MiniMax H3 text encoder selected in GrizzlyMax Settings.'
        self.model_status.setText(text)
        if lines:
            self.status.setText('Check installed models in Settings')
        else:
            self.status.setText('Ready')

    def _sync_encoder_mode(self, checked):
        self.encoder.setEnabled(not checked)
        self.encoder_browse.setEnabled(not checked)
        if not self.loading and not self.scanning_models:
            self.update_model_status({k: range(getattr(self, k).count()) for k in ('diffusion', 'encoder', 'vae')})

    def selected_models(self):
        models = {key: getattr(self, key).currentData() for key in ('diffusion', 'encoder', 'vae')}
        if self.owner is not None and self.reuse_encoder.isChecked():
            from runtime.h3_image.queue_worker import shared_encoder
            models['encoder'] = shared_encoder(self.owner.text_encoder_model.path())
        return models

    def open_output(self):
        path = self.edit_output_dir.text() if self.mode.currentText() == 'Edit' else self.output_dir.text()
        Path(path).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def generate(self):
        if self.busy:
            return
        self.save()
        if self.owner is not None:
            self.owner._enqueue_image_job()
            return
        d = self.gather()
        is_edit = d['mode'] == 'Edit'
        try:
            if not d['prompt'].strip():
                raise H3Error('Enter a prompt.')
            refs = d['references'] if is_edit else []
            if is_edit and not refs:
                raise H3Error('Add an image to edit.')
            if any(not Path(x).is_file() for x in refs):
                raise H3Error('Missing reference image.')
            models = self.selected_models()
            if not all(path and Path(path).is_file() for path in models.values()):
                raise H3Error('Install or select diffusion, encoder and VAE checkpoints in Settings.')
            seed = d['seed'] if d['seed'] >= 0 else random.randrange(2**32)
            out = d['edit_output_dir'] if is_edit else d['output_dir']
            prefix = ''.join(x if x.isalnum() or x in '_-' else '_' for x in d['filename_prefix']) or 'H3_Image'
            self.stop_event.clear()
            self.busy = True
            self.go.setEnabled(False)
            self.cancel.setEnabled(True)
            self.status.setText('Generating…')

            def work():
                try:
                    ensure_backend(self.server, d['backend_python'], d['backend_main'], d['backend_url'], d['lowvram'], self.sig.log.emit, self.stop_event, selected=models)
                    result = generate(
                        d['backend_url'], None, d['prompt'], seed, d['width'], d['height'],
                        {k: Path(v).name for k, v in models.items()}, refs, out, prefix, self.sig.log.emit, self.stop_event, d.get('smart_framing', True), steps=d['steps'], enhanced_details=d.get('enhanced_details', False) and not is_edit, detail_influence=d.get('detail_influence', 0.25)
                    )
                    self.sig.done.emit(result, None)
                except Exception as e:
                    self.sig.done.emit(None, str(e))

            threading.Thread(target=work, daemon=True).start()
            self.job_started = time.monotonic()
            self.current_job_desc = f'{d["mode"]} {d["width"]}×{d["height"]}'
            diff_name = Path(models['diffusion']).name if models.get('diffusion') else '-'
            enc_name = Path(models['encoder']).name if models.get('encoder') else '-'
            vae_name = Path(models['vae']).name if models.get('vae') else '-'
            self.log(f'Job: {d["mode"]} {d["width"]}×{d["height"]} | Seed: {seed} | Steps: {d["steps"]} | References: {len(refs)} | Low VRAM: {"ON" if d["lowvram"] else "OFF"} | Framing helper: {"ON" if d.get("smart_framing", True) else "OFF"}')
            self.log(f'Models: Diffusion={diff_name} | Text encoder={enc_name} | VAE={vae_name}')
            self.log(f'Output folder: {out}')
        except Exception as e:
            self.log(str(e))
            QMessageBox.warning(self, 'Generate', str(e))

    def cancel_wait(self):
        self.stop_event.set()
        extra = f' ({self.current_job_desc})' if self.current_job_desc else ''
        self.log('Stopped waiting; current generation may continue in the engine.' + extra)

    def finished(self, paths, error):
        self.busy = False
        self.go.setEnabled(True)
        self.cancel.setEnabled(False)
        if error:
            self.status.setText('Failed')
            self.log('ERROR: ' + error)
            self.job_started = None
            self.current_job_desc = ''
            QMessageBox.warning(self, 'Generation failed', error)
            return
        self.status.setText('Ready')
        elapsed = ''
        if self.job_started is not None:
            elapsed = f' | Elapsed: {time.monotonic() - self.job_started:.2f}s'
        self.log('Saved: ' + ', '.join(map(str, paths)) + elapsed)
        self.job_started = None
        self.current_job_desc = ''
        if paths:
            pix = QPixmap(str(paths[0]))
            if not pix.isNull():
                self.preview.setPixmap(pix.scaled(max(100, self.preview.width() - 12), max(100, self.preview.height() - 12), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def closeEvent(self, event):
        self.save_timer.stop()
        self.save()
        self.stop_event.set()
        self.server.stop()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    gui = GUI()
    gui.showMaximized()
    sys.exit(app.exec())
