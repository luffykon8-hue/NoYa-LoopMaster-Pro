import os
import random
from PySide6.QtWidgets import (QPushButton, QVBoxLayout, QWidget, QLabel, 
                             QSlider, QComboBox, QFrame, QFileDialog, QHBoxLayout, 
                             QListWidget, QListWidgetItem, QAbstractItemView)
from PySide6.QtCore import Qt, Signal

class DropZone(QPushButton):
    file_dropped = Signal(str)
    def __init__(self, label):
        super().__init__(label)
        self.setAcceptDrops(True)
        self.setMinimumHeight(45)
        self.clicked.connect(self.open_dialog)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()

    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Files")
        for path in paths:
            self.set_file(path)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            self.set_file(path)
        event.accept()

    def set_file(self, path):
        self.setToolTip(path)
        name = os.path.basename(path)
        if len(name) > 25:
            name = name[:15] + "..." + name[-7:]
        self.setText(f"✅ {name}")
        self.file_dropped.emit(path)

class ControlPanel(QFrame):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.video_path = ""
        
        self.img_btn = DropZone("Drop Image/Video Here")
        self.audio_btn = DropZone("Drop Music Here")
        
        self.audio_list = QListWidget()
        
        btn_layout = QHBoxLayout()
        self.shuffle_btn = QPushButton("Shuffle")
        self.shuffle_btn.clicked.connect(self.shuffle_audio_list)
        self.clear_audio_btn = QPushButton("Clear Audio List")
        self.clear_audio_btn.clicked.connect(self.clear_audio_list)
        btn_layout.addWidget(self.shuffle_btn)
        btn_layout.addWidget(self.clear_audio_btn)
        
        self.res_box = QComboBox()
        self.res_box.addItems(["720p", "1080p", "2K", "4K"])
        
        self.proc_box = QComboBox()
        self.proc_box.addItems(["CPU", "GPU (Nvidia)", "GPU (AMD)"])

        layout.addWidget(QLabel("<b>Inputs</b>"))
        
        input_layout = QHBoxLayout()
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.img_btn)
        left_layout.addWidget(self.audio_btn)
        left_layout.addLayout(btn_layout)
        
        input_layout.addLayout(left_layout, 1)
        input_layout.addWidget(self.audio_list, 3)
        
        layout.addLayout(input_layout)
        layout.addWidget(QLabel("<b>Resolution & Hardware</b>"))
        layout.addWidget(self.res_box)
        layout.addWidget(self.proc_box)

        # Connect signals to store the actual full paths
        self.img_btn.file_dropped.connect(self._set_video_path)
        self.audio_btn.file_dropped.connect(self.add_audio_path)

    def _set_video_path(self, path): self.video_path = path
    
    @property
    def audio_paths(self):
        return [self.audio_list.item(i).data(Qt.UserRole) for i in range(self.audio_list.count())]

    def add_audio_path(self, path):
        if path not in self.audio_paths:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            self.audio_list.addItem(item)
            self.audio_list.scrollToBottom()

        self.update_audio_btn_text()

    def update_audio_btn_text(self):
        count = self.audio_list.count()
        if count > 0:
            self.audio_btn.setText(f"✅ {count} Audio Files Added")
        else:
            self.audio_btn.setText("Drop Music Here")
            
    def shuffle_audio_list(self):
        count = self.audio_list.count()
        if count <= 1: return
        
        items = []
        for i in range(count):
            items.append(self.audio_list.takeItem(0))
        
        random.shuffle(items)
        
        for item in items:
            self.audio_list.addItem(item)
        
        self.update_audio_btn_text()

    def clear_audio_list(self):
        self.audio_list.clear()
        self.audio_btn.setText("Drop Music Here")
        self.audio_btn.setToolTip("")