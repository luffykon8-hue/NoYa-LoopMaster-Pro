import sys
import json
import os
import librosa
import numpy as np
import uuid
import hashlib
import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                             QWidget, QPushButton, QProgressBar, QLabel, QCheckBox, QSpinBox, QAbstractSpinBox, QDoubleSpinBox,
                             QColorDialog, QLineEdit, QComboBox, QSlider, QFileDialog, QScrollArea, 
                             QGroupBox, QFrame, QMessageBox, QDialog)
from PySide6.QtGui import QImage, QPixmap, QColor, QFontDatabase, QPainter, QFont, QDesktopServices, QFontMetrics, QPen, QPolygon, QPainterPath, QBrush, QIcon, QAction
from PySide6.QtCore import QThread, Signal, Qt, QRect, QPoint, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
from PySide6.QtCore import QTimer
from interface import ControlPanel
from engine import run_render, RenderLogger
from moviepy.editor import VideoFileClip

SECRET_SALT = "NoYa_Remaster_Secret_2024" # Must match the salt in admin_keygen.py

class Worker(QThread):
    progress = Signal(int)
    error = Signal(str)
    success = Signal()
    def __init__(self, config):
        super().__init__()
        self.config = config
    def run(self):
        try:
            logger = RenderLogger(self.progress.emit, self.isInterruptionRequested)
            run_render(self.config, logger)
            self.success.emit()
        except Exception as e:
            self.error.emit(str(e))

class SpectrumWorker(QThread):
    finished = Signal(object)
    def __init__(self, audio_path, fps=30, num_bars=50):
        super().__init__()
        self.audio_path = audio_path
        self.fps = fps
        self.num_bars = num_bars
    def run(self):
        try:
            y, sr = librosa.load(self.audio_path, sr=None)
            hop_length = int(sr / self.fps)
            stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop_length))
            relevant_bins = int(3000 / (sr / 2048)) 
            bins_per_bar = max(1, relevant_bins // self.num_bars)
            bar_heights_list = []
            for i in range(self.num_bars):
                start_bin = i * bins_per_bar
                end_bin = (i + 1) * bins_per_bar
                bar_heights_list.append(np.mean(stft[start_bin:end_bin, :], axis=0))
            self.finished.emit(np.array(bar_heights_list))
        except Exception as e:
            print(f"Spectrum analysis failed: {e}")
            self.finished.emit(None)

def get_machine_id():
    mac_num = uuid.getnode()
    mac_hex = hex(mac_num)[2:].zfill(12).upper()
    mac_address = ':'.join(mac_hex[i:i+2] for i in range(0, 12, 2))
    return mac_address

class LicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("License Activation")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False) # Prevent closing with 'X'
        self.expiry_date_str = None

        layout = QVBoxLayout(self)

        # Machine ID
        layout.addWidget(QLabel("Please provide this Machine ID to your administrator:"))
        machine_id_layout = QHBoxLayout()
        self.machine_id_field = QLineEdit(get_machine_id())
        self.machine_id_field.setReadOnly(True)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self.copy_machine_id)
        machine_id_layout.addWidget(self.machine_id_field)
        machine_id_layout.addWidget(copy_btn)
        layout.addLayout(machine_id_layout)

        # License Key
        layout.addWidget(QLabel("Enter License Key:"))
        self.key_field = QLineEdit()
        layout.addWidget(self.key_field)

        # Buttons
        button_layout = QHBoxLayout()
        activate_btn = QPushButton("Activate")
        activate_btn.setDefault(True)
        activate_btn.clicked.connect(self.validate_license)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(quit_btn)
        button_layout.addWidget(activate_btn)
        layout.addLayout(button_layout)

    def copy_machine_id(self):
        QApplication.clipboard().setText(self.machine_id_field.text())
        QMessageBox.information(self, "Copied", "Machine ID copied to clipboard.")

    def validate_license(self):
        full_license = self.key_field.text().strip().upper()
        device_id = self.machine_id_field.text()

        if "-" not in full_license or len(full_license.split('-')) != 2:
            QMessageBox.warning(self, "Invalid Format", "The license key format is incorrect. It should be in the format 'YYYYMMDD-KEY'.")
            return

        expiry_date_str, user_key = full_license.split('-')

        # 1. Verify the key against the device ID and expiry date
        data_to_hash = f"{device_id}|{expiry_date_str}|{SECRET_SALT}"
        expected_key = hashlib.sha256(data_to_hash.encode()).hexdigest()[:16].upper()

        if user_key != expected_key:
            QMessageBox.warning(self, "Invalid License", "The license key is not valid for this machine.")
            return
        
        # 2. Check if the license has expired (unless it's permanent)
        if expiry_date_str != "99991231":
            try:
                expiry_date = datetime.datetime.strptime(expiry_date_str, "%Y%m%d")
                if datetime.datetime.now() > expiry_date:
                    QMessageBox.warning(self, "License Expired", f"Your license expired on {expiry_date.strftime('%Y-%m-%d')}.")
                    return
            except ValueError:
                QMessageBox.warning(self, "Invalid License", "The license key contains an invalid date format.")
                return

        # If all checks pass
        QMessageBox.information(self, "Success", "License activated successfully!")
        self.expiry_date_str = expiry_date_str
        self.accept()

class DraggableLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(False)
        self.overlay_text = ""
        self.overlay_font_family = "Arial"
        self.overlay_font_size = 70
        self.overlay_color = QColor("white")
        self.overlay_border_enabled = False
        self.overlay_border_color = QColor("black")
        self.overlay_border_width = 2
        self.overlay_shadow = False
        self.target_height = 1080
        self.rel_pos = [0.5, 0.5]
        self.spectrum_rel_pos = [0.5, 0.85]
        self.base_pixmap = None
        self.image_rect = None
        self.spectrum_preview_enabled = False
        self.spectrum_preview_color = QColor(0, 255, 255)
        self.spectrum_style = "Bars"
        self.spectrum_size = 50
        self.spectrum_thickness = 80
        self.spectrum_sensitivity = 100
        self.active_drag = "text" # 'text' or 'spectrum'
        self.logo_pixmap = None
        self.logo_size = 15
        self.logo_pos = "Top Right"
        self.live_heights = None
        self.progressbar_enabled = False
        self.live_progress = 0.0
        self.progressbar_color = QColor("#2ecc71")
        self.progressbar_height = 2
        self.progressbar_pos = "Bottom"

    def set_pixmap(self, pixmap):
        self.base_pixmap = pixmap
        self.update()

    def set_overlay_settings(self, text, font_family, font_size, color, target_height, shadow=False, 
                             border_enabled=False, border_color=None, border_width=0):
        self.overlay_text = text
        self.overlay_font_family = font_family
        self.overlay_font_size = font_size
        self.overlay_color = color
        self.overlay_border_enabled = border_enabled
        self.overlay_border_color = border_color if border_color else QColor("black")
        self.overlay_border_width = border_width
        self.overlay_shadow = shadow
        self.target_height = target_height
        self.update()

    def set_spectrum_preview(self, enabled, color, style, size, pos_str, thickness, sensitivity):
        self.spectrum_preview_enabled = enabled
        self.spectrum_preview_color = color
        self.spectrum_style = style
        self.spectrum_size = size
        self.spectrum_thickness = thickness
        self.spectrum_sensitivity = sensitivity
        if pos_str != "Custom":
            self.update_spectrum_pos_from_str(pos_str)
        self.update()

    def update_spectrum_pos_from_str(self, pos_str):
        if pos_str == "Bottom": self.spectrum_rel_pos = [0.5, 0.95]
        elif pos_str == "Top": self.spectrum_rel_pos = [0.5, 0.05]
        elif pos_str == "Center": self.spectrum_rel_pos = [0.5, 0.5]

    def set_logo_settings(self, path, size, pos):
        if path and os.path.exists(path):
            self.logo_pixmap = QPixmap(path)
        else:
            self.logo_pixmap = None
        self.logo_size = size
        self.logo_pos = pos
        self.update()

    def set_live_heights(self, heights):
        self.live_heights = heights
        self.update()

    def set_live_progress(self, progress):
        self.live_progress = progress

    def set_progressbar_settings(self, enabled, color, height, pos):
        self.progressbar_enabled = enabled
        self.progressbar_color = color
        self.progressbar_height = height
        self.progressbar_pos = pos
        self.update()

    def paintEvent(self, event):
        if not self.base_pixmap:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w_lbl, h_lbl = self.width(), self.height()
        scaled = self.base_pixmap.scaled(w_lbl, h_lbl, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        x_off = (w_lbl - scaled.width()) // 2
        y_off = (h_lbl - scaled.height()) // 2
        self.image_rect = QRect(x_off, y_off, scaled.width(), scaled.height())
        
        painter.drawPixmap(x_off, y_off, scaled)
        
        if self.spectrum_preview_enabled:
            num_bars = 50
            # Width of the spectrum area (80% of screen width)
            spec_w = int(scaled.width() * 0.8)
            bar_width = spec_w // num_bars
            
            # Some pseudo-random but consistent heights for a nice look
            if self.live_heights is not None:
                heights = self.live_heights
            else:
                heights = [0.1, 0.2, 0.35, 0.4, 0.5, 0.45, 0.3, 0.2, 0.15, 0.25, 0.3, 0.4, 0.5, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.15, 0.2, 0.3, 0.4, 0.3, 0.2, 0.25, 0.35, 0.45, 0.55, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.2, 0.3, 0.4, 0.3, 0.2, 0.1, 0.15, 0.25, 0.35, 0.45, 0.35, 0.25, 0.15, 0.1, 0.05]
            
            # Scale max height based on slider (1-100)
            max_h = int((scaled.height() / 2) * (self.spectrum_size / 100))
            
            if self.spectrum_style == "Line":
                pen = QPen(self.spectrum_preview_color)
                pen.setWidth(max(1, int(bar_width * (self.spectrum_thickness / 100.0))))
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
            elif self.spectrum_style == "Filled Line":
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.spectrum_preview_color)
            else:
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.spectrum_preview_color)

            # Calculate baseline from relative pos
            cx = x_off + (self.spectrum_rel_pos[0] * scaled.width())
            cy = y_off + (self.spectrum_rel_pos[1] * scaled.height())
            
            start_x = int(cx - (spec_w / 2))

            drawn_w = max(1, int(bar_width * (self.spectrum_thickness / 100.0)))
            offset = (bar_width - drawn_w) // 2

            prev_point = None
            filled_line_points = []

            sens_factor = self.spectrum_sensitivity / 100.0

            for i in range(num_bars):
                if self.live_heights is not None:
                    # Scale live data to match engine.py visual logic
                    # engine.py uses: raw * 15 * (size/50)
                    # Here max_h accounts for size. We approximate pixel height relative to 1080p.
                    bh = int(((heights[i] * sens_factor * 6) / 270.0) * max_h)
                else:
                    bh = int(heights[i] * sens_factor * max_h)
                bx = start_x + i * bar_width + offset
                
                if self.spectrum_style == "Bars":
                    # Check if position is "Top" (upper half of screen) to invert direction
                    if self.spectrum_rel_pos[1] < 0.4:
                        # Grow Down
                        painter.drawRect(bx, int(cy), drawn_w, bh)
                    else:
                        # Grow Up
                        painter.drawRect(bx, int(cy - bh), drawn_w, bh)
                elif self.spectrum_style == "Blocks":
                    block_h = max(2, int(max_h * 0.02)) # 2% of max height
                    gap = max(1, int(block_h * 0.5))
                    for b in range(0, bh, block_h + gap):
                        if self.spectrum_rel_pos[1] < 0.4:
                            painter.drawRect(bx, int(cy + b), drawn_w, block_h)
                        else:
                            painter.drawRect(bx, int(cy - b - block_h), drawn_w, block_h)
                elif self.spectrum_style == "Line":
                    center_x = bx + drawn_w // 2
                    y = int(cy + bh) if self.spectrum_rel_pos[1] < 0.4 else int(cy - bh)
                    curr_point = QPoint(center_x, y)
                    if prev_point:
                        painter.drawLine(prev_point, curr_point)
                    prev_point = curr_point
                elif self.spectrum_style == "Filled Line":
                    center_x = bx + drawn_w // 2
                    y = int(cy + bh) if self.spectrum_rel_pos[1] < 0.4 else int(cy - bh)
                    filled_line_points.append(QPoint(center_x, y))
                elif self.spectrum_style == "Mirrored":
                    # Grow Up and Down
                    painter.drawRect(bx, int(cy - bh), drawn_w, bh * 2)
                elif self.spectrum_style == "Dots":
                    # Just the top
                    painter.drawRect(bx, int(cy - bh), drawn_w, 4)
                elif self.spectrum_style == "Circle":
                    radius = 40 * (self.spectrum_size / 50.0)
                    center = QPoint(int(cx), int(cy))
                    painter.save()
                    painter.translate(center)
                    painter.rotate(i * (360.0 / num_bars))
                    # Draw bar extending outwards from radius
                    painter.drawRect(0, int(-radius - bh), drawn_w, bh)
                    painter.restore()
            
            if self.spectrum_style == "Filled Line" and filled_line_points:
                # Close the polygon
                base_y = int(cy)
                first_x = filled_line_points[0].x()
                last_x = filled_line_points[-1].x()
                
                polygon = QPolygon(filled_line_points)
                polygon.append(QPoint(last_x, base_y))
                polygon.append(QPoint(first_x, base_y))
                painter.drawPolygon(polygon)

        if self.logo_pixmap and self.image_rect:
            # Calculate size relative to video height (percentage)
            target_h = max(1, int(self.image_rect.height() * (self.logo_size / 100)))
            scaled_logo = self.logo_pixmap.scaledToHeight(target_h, Qt.SmoothTransformation)
            
            margin = int(self.image_rect.height() * 0.02) # 2% margin
            lx, ly = 0, 0
            
            # Vertical Position
            if "Top" in self.logo_pos: 
                ly = self.image_rect.top() + margin
            elif "Bottom" in self.logo_pos: 
                ly = self.image_rect.bottom() - scaled_logo.height() - margin
            else: 
                ly = self.image_rect.center().y() - scaled_logo.height() // 2

            # Horizontal Position
            if "Left" in self.logo_pos: 
                lx = self.image_rect.left() + margin
            elif "Right" in self.logo_pos: 
                lx = self.image_rect.right() - scaled_logo.width() - margin
            else: 
                lx = self.image_rect.center().x() - scaled_logo.width() // 2
                
            painter.drawPixmap(lx, ly, scaled_logo)

        if self.progressbar_enabled and self.image_rect:
            bar_h = max(2, int(self.image_rect.height() * (self.progressbar_height / 100)))
            if self.progressbar_pos == "Top":
                bar_y = self.image_rect.top()
            else:
                bar_y = self.image_rect.bottom() - bar_h
            
            bar_w = int(self.image_rect.width() * self.live_progress)
            painter.fillRect(self.image_rect.left(), bar_y, bar_w, bar_h, self.progressbar_color)

        if self.overlay_text:
            scale_factor = scaled.height() / self.target_height
            font = QFont(self.overlay_font_family)
            font.setPixelSize(max(1, int(self.overlay_font_size * scale_factor)))
            painter.setFont(font)
            painter.setPen(self.overlay_color)
            
            cx = x_off + (self.rel_pos[0] * scaled.width())
            cy = y_off + (self.rel_pos[1] * scaled.height())
            
            fm = QFontMetrics(font)
            b_rect = fm.boundingRect(self.overlay_text)
            draw_rect = QRect(0, 0, b_rect.width() + 20, b_rect.height() + 20)
            draw_rect.moveCenter(QPoint(int(cx), int(cy)))
            
            if self.overlay_shadow:
                shadow_offset = max(1, int(self.overlay_font_size * scale_factor * 0.05))
                shadow_rect = draw_rect.translated(shadow_offset, shadow_offset)
                painter.setPen(QColor(0, 0, 0, 180))
                painter.drawText(shadow_rect, Qt.AlignCenter, self.overlay_text)
            
            if self.overlay_border_enabled:
                # Use QPainterPath for stroke/border
                path = QPainterPath()
                # Calculate baseline origin to center text roughly where drawText would
                text_w = fm.horizontalAdvance(self.overlay_text)
                # Center X: cx - half width
                # Center Y: cy + half ascent - half descent (approximate visual center)
                origin_x = cx - text_w / 2
                origin_y = cy + (fm.ascent() - fm.descent()) / 2
                path.addText(QPoint(int(origin_x), int(origin_y)), font, self.overlay_text)
                
                pen = QPen(self.overlay_border_color)
                # Scale border width for preview
                pen.setWidthF(max(1, self.overlay_border_width * scale_factor))
                painter.strokePath(path, pen)
                painter.fillPath(path, QBrush(self.overlay_color))
            else:
                painter.drawText(draw_rect, Qt.AlignCenter, self.overlay_text)

    def mouseMoveEvent(self, event):
        if self.image_rect:
            pos = event.pos()
            x = max(self.image_rect.left(), min(pos.x(), self.image_rect.right()))
            y = max(self.image_rect.top(), min(pos.y(), self.image_rect.bottom()))
            
            rel_x = (x - self.image_rect.left()) / self.image_rect.width()
            rel_y = (y - self.image_rect.top()) / self.image_rect.height()
            
            if self.active_drag == "text":
                self.rel_pos = [rel_x, rel_y]
                if hasattr(self.parent(), 'on_text_dragged'):
                    self.parent().on_text_dragged()
            elif self.active_drag == "spectrum":
                self.spectrum_rel_pos = [rel_x, rel_y]
                if hasattr(self.parent(), 'on_spectrum_dragged'):
                    self.parent().on_spectrum_dragged()
            
            self.update()

class MainWindow(QMainWindow):
    def __init__(self, expiry_date_str=None):
        super().__init__()
        self.setWindowTitle("LoopMaster Pro")
        self.version = "1.0.0"
        self.setMinimumSize(900, 600)
        self.current_output_path = ""
        self.logo_path = ""
        self.is_playing = False
        self.audio_queue = []
        self.spectrum_data = None
        self.spectrum_fps = 30
        self.current_smooth_heights = None
        self.preview_timer = QTimer()
        self.preview_timer.timeout.connect(self.update_playback_loop)

        # Media Player Setup
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0) # Mute video audio
        self.media_player.setAudioOutput(self.audio_output)
        self.video_sink = QVideoSink()
        self.media_player.setVideoSink(self.video_sink)
        self.video_sink.videoFrameChanged.connect(self.handle_video_frame)

        self.music_player = QMediaPlayer()
        self.music_output = QAudioOutput()
        self.music_output.setVolume(1.0)
        self.music_player.setAudioOutput(self.music_output)
        self.music_player.mediaStatusChanged.connect(self.handle_music_status)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        
        self.statusBar().showMessage("Ready")
        self.license_status_label = QLabel("")
        self.statusBar().addPermanentWidget(self.license_status_label)
        self.set_license_status(expiry_date_str)
        
        # Sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(380)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll Area for Controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(15)
        scroll_layout.setContentsMargins(10, 10, 10, 10)

        # 1. Main Inputs
        self.controls = ControlPanel()
        scroll_layout.addWidget(self.controls)

        # 2. Duration Settings
        dur_group = QGroupBox("Duration Settings")
        dur_layout = QVBoxLayout()
        dur_layout.addWidget(QLabel("Manual Duration (Minutes):"))
        
        dur_row = QHBoxLayout()
        dur_minus = QPushButton("-")
        dur_minus.setFixedSize(30, 30)
        self.dur_input = QSpinBox()
        self.dur_input.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.dur_input.setAlignment(Qt.AlignCenter)
        self.dur_input.setRange(1, 180)
        self.dur_input.setValue(3)
        dur_plus = QPushButton("+")
        dur_plus.setFixedSize(30, 30)
        dur_minus.clicked.connect(self.dur_input.stepDown)
        dur_plus.clicked.connect(self.dur_input.stepUp)
        
        dur_row.addWidget(dur_minus)
        dur_row.addWidget(self.dur_input)
        dur_row.addWidget(dur_plus)
        dur_layout.addLayout(dur_row)
        dur_group.setLayout(dur_layout)
        scroll_layout.addWidget(dur_group)
        
        # 3. Audio Spectrum
        spec_group = QGroupBox("Audio Spectrum")
        spec_layout = QVBoxLayout()
        self.spectrum_chk = QCheckBox("Enable Audio Spectrum")
        self.spectrum_color = QColor(0, 255, 255)  # Default Cyan
        
        self.spec_style_box = QComboBox()
        self.spec_style_box.addItems(["Bars", "Mirrored", "Dots", "Circle", "Line", "Filled Line", "Blocks"])
        
        self.spec_size_label = QLabel("Size: 50%")
        self.spec_size_slider = QSlider(Qt.Horizontal)
        self.spec_size_slider.setRange(10, 150)
        self.spec_size_slider.setValue(50)
        self.spec_size_slider.valueChanged.connect(lambda v: self.spec_size_label.setText(f"Size: {v}%"))
        
        self.spec_thick_label = QLabel("Thickness: 80%")
        self.spec_thick_slider = QSlider(Qt.Horizontal)
        self.spec_thick_slider.setRange(10, 100)
        self.spec_thick_slider.setValue(80)
        self.spec_thick_slider.valueChanged.connect(lambda v: self.spec_thick_label.setText(f"Thickness: {v}%"))
        
        self.spec_smooth_label = QLabel("Smoothness: 0%")
        self.spec_smooth_slider = QSlider(Qt.Horizontal)
        self.spec_smooth_slider.setRange(0, 95)
        self.spec_smooth_slider.setValue(0)
        self.spec_smooth_slider.valueChanged.connect(lambda v: self.spec_smooth_label.setText(f"Smoothness: {v}%"))
        
        self.spec_sens_label = QLabel("Sensitivity: 100%")
        self.spec_sens_slider = QSlider(Qt.Horizontal)
        self.spec_sens_slider.setRange(10, 300)
        self.spec_sens_slider.setValue(100)
        self.spec_sens_slider.valueChanged.connect(lambda v: self.spec_sens_label.setText(f"Sensitivity: {v}%"))
        
        self.spec_pos_box = QComboBox()
        self.spec_pos_box.addItems(["Bottom", "Top", "Center", "Custom"])
        
        self.color_btn = QPushButton("Select Spectrum Color")
        self.color_btn.setStyleSheet(f"background-color: {self.spectrum_color.name()}; color: #000;")
        self.color_btn.clicked.connect(self.choose_color)
        spec_layout.addWidget(self.spectrum_chk)
        spec_layout.addWidget(QLabel("Style:"))
        spec_layout.addWidget(self.spec_style_box)
        spec_layout.addWidget(self.spec_size_label)
        spec_layout.addWidget(self.spec_size_slider)
        spec_layout.addWidget(self.spec_thick_label)
        spec_layout.addWidget(self.spec_thick_slider)
        spec_layout.addWidget(self.spec_smooth_label)
        spec_layout.addWidget(self.spec_smooth_slider)
        spec_layout.addWidget(self.spec_sens_label)
        spec_layout.addWidget(self.spec_sens_slider)
        spec_layout.addWidget(QLabel("Position:"))
        spec_layout.addWidget(self.spec_pos_box)
        spec_layout.addWidget(self.color_btn)
        spec_group.setLayout(spec_layout)
        scroll_layout.addWidget(spec_group)

        # 4. Text Overlay
        text_group = QGroupBox("Text Overlay")
        text_layout = QVBoxLayout()
        
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Enter Title/Artist...")
        
        self.text_color = QColor("white")
        self.text_color_btn = QPushButton("Select Text Color")
        self.text_color_btn.setStyleSheet(f"background-color: {self.text_color.name()}; color: #000;")
        self.text_color_btn.clicked.connect(self.choose_text_color)

        self.font_box = QComboBox()
        self.font_box.addItems(QFontDatabase.families())
        self.font_box.setCurrentText("Arial")
        
        self.font_size_label = QLabel("Font Size: 70")
        self.font_size_slider = QSlider(Qt.Horizontal)
        self.font_size_slider.setRange(10, 300)
        self.font_size_slider.setValue(70)
        self.font_size_slider.valueChanged.connect(lambda v: self.font_size_label.setText(f"Font Size: {v}"))
        
        self.text_pos_box = QComboBox()
        self.text_pos_box.addItems(["Center", "Top", "Bottom", "Custom"])
        
        self.text_shadow_chk = QCheckBox("Enable Shadow")
        self.text_shadow_chk.stateChanged.connect(self.apply_text_preview)
        
        # Border Controls
        self.text_border_chk = QCheckBox("Enable Border")
        self.text_border_chk.stateChanged.connect(self.apply_text_preview)
        
        border_row = QHBoxLayout()
        self.text_border_color = QColor("black")
        self.text_border_color_btn = QPushButton("Border Color")
        self.text_border_color_btn.setStyleSheet(f"background-color: {self.text_border_color.name()}; color: #fff;")
        self.text_border_color_btn.clicked.connect(self.choose_border_color)
        
        self.text_border_width = QSpinBox()
        self.text_border_width.setRange(1, 20)
        self.text_border_width.setValue(2)
        self.text_border_width.valueChanged.connect(self.apply_text_preview)
        
        border_row.addWidget(self.text_border_color_btn)
        border_row.addWidget(QLabel("Width:"))
        border_row.addWidget(self.text_border_width)
        
        text_layout.addWidget(QLabel("Text Content:"))
        text_layout.addWidget(self.text_input)
        text_layout.addWidget(self.text_color_btn)
        text_layout.addWidget(QLabel("Font Family:"))
        text_layout.addWidget(self.font_box)
        text_layout.addWidget(self.font_size_label)
        text_layout.addWidget(self.font_size_slider)
        text_layout.addWidget(QLabel("Position:"))
        text_layout.addWidget(self.text_pos_box)
        text_layout.addWidget(self.text_shadow_chk)
        text_layout.addWidget(self.text_border_chk)
        text_layout.addLayout(border_row)
        text_group.setLayout(text_layout)
        scroll_layout.addWidget(text_group)

        # 5. Logo Overlay
        logo_group = QGroupBox("Logo Overlay")
        logo_layout = QVBoxLayout()
        
        self.logo_btn = QPushButton("Select Logo Image")
        self.logo_btn.clicked.connect(self.select_logo)
        
        self.logo_size_label = QLabel("Logo Size: 15%")
        self.logo_size_slider = QSlider(Qt.Horizontal)
        self.logo_size_slider.setRange(1, 50)
        self.logo_size_slider.setValue(15)
        self.logo_size_slider.valueChanged.connect(lambda v: self.logo_size_label.setText(f"Logo Size: {v}%"))
        self.logo_size_slider.valueChanged.connect(self.apply_logo_preview)
        
        self.logo_pos_box = QComboBox()
        self.logo_pos_box.addItems(["Top Right", "Top Left", "Bottom Right", "Bottom Left", "Center"])
        self.logo_pos_box.currentTextChanged.connect(self.apply_logo_preview)
        
        logo_layout.addWidget(self.logo_btn)
        logo_layout.addWidget(self.logo_size_label)
        logo_layout.addWidget(self.logo_size_slider)
        logo_layout.addWidget(QLabel("Position:"))
        logo_layout.addWidget(self.logo_pos_box)
        logo_group.setLayout(logo_layout)
        scroll_layout.addWidget(logo_group)

        # 7. Progress Bar Overlay
        prog_group = QGroupBox("Progress Bar Overlay")
        prog_layout = QVBoxLayout()
        
        self.prog_chk = QCheckBox("Enable Progress Bar")
        self.prog_chk.stateChanged.connect(self.apply_prog_preview)
        
        self.prog_color = QColor("#2ecc71")
        self.prog_color_btn = QPushButton("Bar Color")
        self.prog_color_btn.setStyleSheet(f"background-color: {self.prog_color.name()}; color: #000;")
        self.prog_color_btn.clicked.connect(self.choose_prog_color)
        
        self.prog_height_label = QLabel("Height: 2%")
        self.prog_height_slider = QSlider(Qt.Horizontal)
        self.prog_height_slider.setRange(1, 20)
        self.prog_height_slider.setValue(2)
        self.prog_height_slider.valueChanged.connect(lambda v: self.prog_height_label.setText(f"Height: {v}%"))
        self.prog_height_slider.valueChanged.connect(self.apply_prog_preview)
        
        self.prog_pos_box = QComboBox()
        self.prog_pos_box.addItems(["Bottom", "Top"])
        self.prog_pos_box.currentTextChanged.connect(self.apply_prog_preview)
        
        prog_layout.addWidget(self.prog_chk)
        prog_layout.addWidget(self.prog_color_btn)
        prog_layout.addWidget(self.prog_height_label)
        prog_layout.addWidget(self.prog_height_slider)
        prog_layout.addWidget(QLabel("Position:"))
        prog_layout.addWidget(self.prog_pos_box)
        prog_group.setLayout(prog_layout)
        scroll_layout.addWidget(prog_group)

        # 7. Presets
        preset_group = QGroupBox("Presets")
        preset_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save Preset")
        self.load_btn = QPushButton("Load Preset")
        preset_layout.addWidget(self.save_btn)
        preset_layout.addWidget(self.load_btn)
        preset_group.setLayout(preset_layout)
        scroll_layout.addWidget(preset_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        side_layout.addWidget(scroll)

        # Bottom Fixed Area
        bottom_area = QWidget()
        bottom_layout = QVBoxLayout(bottom_area)
        bottom_layout.setContentsMargins(10, 10, 10, 10)
        self.render_btn = QPushButton("RENDER PROJECT")
        self.render_btn.setMinimumHeight(50)
        self.render_btn.setStyleSheet("background-color: #2ecc71; font-weight: bold; color: white; border-radius: 5px;")
        
        self.play_btn = QPushButton("▶ Play Preview")
        self.play_btn.setMinimumHeight(50)
        self.play_btn.setStyleSheet("background-color: #f39c12; font-weight: bold; color: white; border-radius: 5px;")
        self.play_btn.clicked.connect(self.toggle_preview)
        
        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setMinimumHeight(50)
        self.cancel_btn.setStyleSheet("background-color: #e74c3c; font-weight: bold; color: white; border-radius: 5px;")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_render)
        
        self.open_folder_btn = QPushButton("OPEN FOLDER")
        self.open_folder_btn.setMinimumHeight(50)
        self.open_folder_btn.setStyleSheet("background-color: #3498db; font-weight: bold; color: white; border-radius: 5px;")
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        self.open_folder_btn.setVisible(False)
        
        self.pbar = QProgressBar()
        
        bottom_layout.addWidget(self.pbar)
        
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.play_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.render_btn)
        btn_layout.addWidget(self.open_folder_btn)
        bottom_layout.addLayout(btn_layout)
        
        side_layout.addWidget(bottom_area)
        
        # Main Body (Preview Area)
        self.preview_area = DraggableLabel("Media Preview\n(Drop files to see preview)", self)
        self.preview_area.setAlignment(Qt.AlignCenter)
        self.preview_area.setStyleSheet("background-color: #1a1a1a; border-radius: 10px; color: #555; font-size: 18px;")
        
        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.preview_area, 1)
        
        self.render_btn.clicked.connect(self.start_task)
        self.save_btn.clicked.connect(self.save_preset)
        self.load_btn.clicked.connect(self.load_preset)
        self.controls.img_btn.file_dropped.connect(self.update_preview)
        
        # Live preview connections
        self.spectrum_chk.stateChanged.connect(self.update_spectrum_preview)
        self.text_input.textChanged.connect(self.apply_text_preview)
        self.font_box.currentTextChanged.connect(self.apply_text_preview)
        self.font_size_slider.valueChanged.connect(self.apply_text_preview)
        self.text_pos_box.currentTextChanged.connect(self.apply_text_preview)

    def toggle_preview(self):
        if self.is_playing:
            self.stop_preview()
        else:
            self.start_preview()

    def start_preview(self):
        # Audio
        self.audio_queue = list(self.controls.audio_paths)
        if self.audio_queue:
            self.play_next_song()
        
        # Video
        video_path = self.controls.video_path
        if video_path and video_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            self.media_player.setSource(QUrl.fromLocalFile(video_path))
            self.media_player.setLoops(-1) # Infinite loop
            self.media_player.play()
            
        self.play_btn.setText("⏹ Stop Preview")
        self.is_playing = True

    def stop_preview(self):
        self.preview_timer.stop()
        self.media_player.stop()
        self.music_player.stop()
        self.play_btn.setText("▶ Play Preview")
        self.is_playing = False
        self.preview_area.set_live_heights(None)
        self.preview_area.set_live_progress(0)
        self.current_smooth_heights = None

    def play_next_song(self):
        if self.audio_queue:
            song = self.audio_queue.pop(0)
            self.music_player.setSource(QUrl.fromLocalFile(song))
            self.music_player.play()
            self.audio_queue.append(song) # Loop playlist
            
            # Start spectrum analysis
            self.spectrum_data = None
            self.spec_worker = SpectrumWorker(song, fps=self.spectrum_fps)
            self.spec_worker.finished.connect(self.on_spectrum_ready)
            self.spec_worker.start()
            
            self.preview_timer.start(int(1000/self.spectrum_fps))

    def on_spectrum_ready(self, data):
        self.spectrum_data = data

    def update_playback_loop(self):
        if not self.is_playing:
            return

        duration = self.music_player.duration()
        if duration > 0:
            pos = self.music_player.position()
            progress = pos / duration
            self.preview_area.set_live_progress(progress)
        else:
            self.preview_area.set_live_progress(0)

        if self.spectrum_data is not None:
            pos_ms = self.music_player.position()
            frame = int((pos_ms / 1000.0) * self.spectrum_fps)
            if frame < self.spectrum_data.shape[1]:
                raw_heights = self.spectrum_data[:, frame]
                
                smoothness = self.spec_smooth_slider.value()
                if smoothness > 0:
                    alpha = 1 - (smoothness / 100.0)
                    if self.current_smooth_heights is None:
                        self.current_smooth_heights = raw_heights
                    else:
                        self.current_smooth_heights = alpha * raw_heights + (1 - alpha) * self.current_smooth_heights
                    heights = self.current_smooth_heights
                else:
                    heights = raw_heights
                    self.current_smooth_heights = None
                self.preview_area.set_live_heights(heights) # This calls update()
        else:
            # If spectrum is off, we still need to update for the progress bar
            self.preview_area.update()

    def handle_music_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.play_next_song()

    def handle_video_frame(self):
        frame = self.video_sink.videoFrame()
        if frame.isValid():
            image = frame.toImage()
            self.preview_area.set_pixmap(QPixmap.fromImage(image))

    def update_preview(self, path):
        self.stop_preview()
        pixmap = None
        if path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            pixmap = QPixmap(path)
        elif path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            try:
                clip = VideoFileClip(path)
                frame = clip.get_frame(0)
                h, w, c = frame.shape
                qImg = QImage(frame.data, w, h, w * 3, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qImg)
                clip.close()
            except Exception as e:
                self.preview_area.setText(f"Preview Error: {e}")
        
        if pixmap:
            self.preview_area.set_pixmap(pixmap)

    def on_text_dragged(self):
        self.text_pos_box.setCurrentText("Custom")
        
    def on_spectrum_dragged(self):
        self.spec_pos_box.setCurrentText("Custom")

    def set_active_drag(self, target):
        self.preview_area.active_drag = target

    def apply_text_preview(self):
        self.set_active_drag("text")
        res_map = {"720p": 720, "1080p": 1080, "2K": 1440, "4K": 2160}
        target_h = res_map.get(self.controls.res_box.currentText(), 1080)
        
        pos_selection = self.text_pos_box.currentText()
        if pos_selection == "Center":
            self.preview_area.rel_pos = [0.5, 0.5]
        elif pos_selection == "Top":
            self.preview_area.rel_pos = [0.5, 0.1]
        elif pos_selection == "Bottom":
            self.preview_area.rel_pos = [0.5, 0.9]
            
        self.preview_area.set_overlay_settings(
            self.text_input.text(),
            self.font_box.currentText(),
            self.font_size_slider.value(),
            self.text_color,
            target_h,
            self.text_shadow_chk.isChecked(),
            self.text_border_chk.isChecked(),
            self.text_border_color,
            self.text_border_width.value()
        )

    def update_spectrum_preview(self):
        self.preview_area.active_drag = "spectrum"
        enabled = self.spectrum_chk.isChecked()
        color = self.spectrum_color
        style = self.spec_style_box.currentText()
        size = self.spec_size_slider.value()
        pos = self.spec_pos_box.currentText()
        thickness = self.spec_thick_slider.value()
        sensitivity = self.spec_sens_slider.value()
        self.preview_area.set_spectrum_preview(enabled, color, style, size, pos, thickness, sensitivity)
        
        # Connect signals for live update
        if not hasattr(self, 'spec_signals_connected'):
            self.spec_style_box.currentTextChanged.connect(self.update_spectrum_preview)
            self.spec_size_slider.valueChanged.connect(self.update_spectrum_preview)
            self.spec_thick_slider.valueChanged.connect(self.update_spectrum_preview)
            self.spec_smooth_slider.valueChanged.connect(self.update_spectrum_preview)
            self.spec_sens_slider.valueChanged.connect(self.update_spectrum_preview)
            self.spec_pos_box.currentTextChanged.connect(self.update_spectrum_preview)
            self.spec_signals_connected = True

    def choose_color(self):
        color = QColorDialog.getColor(self.spectrum_color, self, "Choose Spectrum Color")
        if color.isValid():
            self.spectrum_color = color
            self.color_btn.setStyleSheet(f"background-color: {color.name()}; color: #000;")
            self.update_spectrum_preview()

    def choose_text_color(self):
        color = QColorDialog.getColor(self.text_color, self, "Choose Text Color")
        if color.isValid():
            self.text_color = color
            self.text_color_btn.setStyleSheet(f"background-color: {color.name()}; color: #000;")
            self.apply_text_preview()

    def choose_border_color(self):
        color = QColorDialog.getColor(self.text_border_color, self, "Choose Border Color")
        if color.isValid():
            self.text_border_color = color
            self.text_border_color_btn.setStyleSheet(f"background-color: {color.name()}; color: #fff;")
            self.apply_text_preview()

    def select_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Logo", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.logo_path = path
            self.logo_btn.setText(f"Logo: {os.path.basename(path)}")
            self.apply_logo_preview()

    def choose_prog_color(self):
        color = QColorDialog.getColor(self.prog_color, self, "Choose Bar Color")
        if color.isValid():
            self.prog_color = color
            self.prog_color_btn.setStyleSheet(f"background-color: {color.name()}; color: #000;")
            self.apply_prog_preview()

    def apply_prog_preview(self):
        self.preview_area.set_progressbar_settings(
            self.prog_chk.isChecked(),
            self.prog_color,
            self.prog_height_slider.value(),
            self.prog_pos_box.currentText()
        )

    def apply_logo_preview(self):
        self.preview_area.set_logo_settings(self.logo_path, self.logo_size_slider.value(), self.logo_pos_box.currentText())

    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Preset", "", "JSON Files (*.json)")
        if not path: return
        
        data = {
            "video_path": self.controls.video_path,
            "audio_paths": self.controls.audio_paths,
            "resolution": self.controls.res_box.currentText(),
            "processor": self.controls.proc_box.currentText(),
            "duration": self.dur_input.value(),
            "spectrum": self.spectrum_chk.isChecked(),
            "spectrum_color": self.spectrum_color.name(),
            "spectrum_style": self.spec_style_box.currentText(),
            "spectrum_size": self.spec_size_slider.value(),
            "spectrum_pos": self.spec_pos_box.currentText(),
            "spectrum_thickness": self.spec_thick_slider.value(),
            "spectrum_smoothness": self.spec_smooth_slider.value(),
            "spectrum_sensitivity": self.spec_sens_slider.value(),
            "spectrum_custom_pos": self.preview_area.spectrum_rel_pos,
            "text": self.text_input.text(),
            "text_color": self.text_color.name(),
            "font": self.font_box.currentText(),
            "text_shadow": self.text_shadow_chk.isChecked(),
            "text_border_enabled": self.text_border_chk.isChecked(),
            "text_border_color": self.text_border_color.name(),
            "text_border_width": self.text_border_width.value(),
            "font_size": self.font_size_slider.value(),
            "text_pos": self.text_pos_box.currentText(),
            "custom_pos": self.preview_area.rel_pos,
            "logo_path": self.logo_path,
            "logo_size": self.logo_size_slider.value(),
            "logo_pos": self.logo_pos_box.currentText(),
            "progressbar_enabled": self.prog_chk.isChecked(),
            "progressbar_color": [self.prog_color.red(), self.prog_color.green(), self.prog_color.blue()],
            "progressbar_height": self.prog_height_slider.value(),
            "progressbar_pos": self.prog_pos_box.currentText()
        }
        
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
        self.statusBar().showMessage(f"Preset saved: {os.path.basename(path)}", 5000)

    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Preset", "", "JSON Files (*.json)")
        if not path: return
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            if data.get("video_path") and os.path.exists(data["video_path"]):
                self.controls.img_btn.set_file(data["video_path"])
            
            if data.get("audio_paths"):
                self.controls.clear_audio_list()
                for path in data["audio_paths"]:
                    if os.path.exists(path):
                        self.controls.audio_btn.set_file(path)
            elif data.get("audio_path") and os.path.exists(data["audio_path"]):
                self.controls.clear_audio_list()
                self.controls.audio_btn.set_file(data["audio_path"])
                
            self.controls.res_box.setCurrentText(data.get("resolution", "1080p"))
            self.controls.proc_box.setCurrentText(data.get("processor", "CPU"))
            self.dur_input.setValue(data.get("duration", 3))
            self.spectrum_chk.setChecked(data.get("spectrum", False))
            if data.get("spectrum_color"):
                self.spectrum_color = QColor(data["spectrum_color"])
                self.color_btn.setStyleSheet(f"background-color: {self.spectrum_color.name()}; color: #000;")
            self.spec_style_box.setCurrentText(data.get("spectrum_style", "Bars"))
            self.spec_size_slider.setValue(data.get("spectrum_size", 50))
            self.spec_thick_slider.setValue(data.get("spectrum_thickness", 80))
            self.spec_smooth_slider.setValue(data.get("spectrum_smoothness", 0))
            self.spec_sens_slider.setValue(data.get("spectrum_sensitivity", 100))
            self.spec_pos_box.setCurrentText(data.get("spectrum_pos", "Bottom"))
            if data.get("spectrum_custom_pos"): self.preview_area.spectrum_rel_pos = data["spectrum_custom_pos"]
            self.text_input.setText(data.get("text", ""))
            if data.get("text_color"):
                self.text_color = QColor(data["text_color"])
                self.text_color_btn.setStyleSheet(f"background-color: {self.text_color.name()}; color: #000;")
            self.text_shadow_chk.setChecked(data.get("text_shadow", False))
            self.text_border_chk.setChecked(data.get("text_border_enabled", False))
            if data.get("text_border_color"):
                self.text_border_color = QColor(data["text_border_color"])
                self.text_border_color_btn.setStyleSheet(f"background-color: {self.text_border_color.name()}; color: #fff;")
            self.text_border_width.setValue(data.get("text_border_width", 2))
            self.font_box.setCurrentText(data.get("font", "Arial"))
            self.font_size_slider.setValue(data.get("font_size", 70))
            self.text_pos_box.setCurrentText(data.get("text_pos", "Center"))
            if data.get("custom_pos"): self.preview_area.rel_pos = data["custom_pos"]
            
            if data.get("logo_path") and os.path.exists(data["logo_path"]):
                self.logo_path = data["logo_path"]
                self.logo_btn.setText(f"Logo: {os.path.basename(self.logo_path)}")
            self.logo_size_slider.setValue(data.get("logo_size", 15))
            self.logo_pos_box.setCurrentText(data.get("logo_pos", "Top Right"))
            
            self.prog_chk.setChecked(data.get("progressbar_enabled", False))
            if data.get("progressbar_color"):
                self.prog_color = QColor(data["progressbar_color"][0], data["progressbar_color"][1], data["progressbar_color"][2]) if isinstance(data["progressbar_color"], list) else QColor(data["progressbar_color"])
                self.prog_color_btn.setStyleSheet(f"background-color: {self.prog_color.name()}; color: #000;")
            self.prog_height_slider.setValue(data.get("progressbar_height", 2))
            self.prog_pos_box.setCurrentText(data.get("progressbar_pos", "Bottom"))
            
            self.update_spectrum_preview()
            self.apply_logo_preview()
            self.apply_prog_preview()
            if self.text_input.text(): self.apply_text_preview()
            self.statusBar().showMessage(f"Preset loaded: {os.path.basename(path)}", 5000)
        except Exception as e:
            print(f"Error loading preset: {e}")
            self.statusBar().showMessage(f"Error loading preset: {e}", 5000)

    def cancel_render(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Cancelling render...")

    def set_license_status(self, expiry_date_str):
        if not expiry_date_str:
            self.license_status_label.setText("License: N/A")
            return

        if expiry_date_str == "99991231":
            self.license_status_label.setText("License valid until: Permanent")
            return
        
        try:
            expiry_date = datetime.datetime.strptime(expiry_date_str, "%Y%m%d").date()
            today = datetime.date.today()
            remaining_days = (expiry_date - today).days

            if remaining_days < 0:
                self.license_status_label.setText("License: Expired")
            else:
                self.license_status_label.setText(f"License valid until: {expiry_date.strftime('%Y-%m-%d')} ({remaining_days} days left)")
        except ValueError:
            self.license_status_label.setText("License: Invalid Date")

    def open_output_folder(self):
        if self.current_output_path:
            folder_path = os.path.dirname(os.path.abspath(self.current_output_path))
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))

    def start_task(self):
        self.stop_preview()
        if not self.controls.video_path or not self.controls.audio_paths:
            print("Error: Please drop both a video/image and an audio file first.")
            return

        out_path, _ = QFileDialog.getSaveFileName(self, "Save Output Video", "output.mp4", "Video Files (*.mp4)")
        if not out_path:
            return
        self.current_output_path = out_path

        text_pos = self.text_pos_box.currentText()
        if text_pos == "Custom":
            text_pos = self.preview_area.rel_pos
            
        spec_pos = self.spec_pos_box.currentText()
        if spec_pos == "Custom":
            spec_pos = self.preview_area.spectrum_rel_pos
            
        config = {
            "video": self.controls.video_path,
            "audio": self.controls.audio_paths,
            "res": self.controls.res_box.currentText(),
            "processor": self.controls.proc_box.currentText(),
            "duration": self.dur_input.value() * 60,
            "spectrum": self.spectrum_chk.isChecked(),
            "spectrum_style": self.spec_style_box.currentText(),
            "spectrum_size": self.spec_size_slider.value(),
            "spectrum_thickness": self.spec_thick_slider.value(),
            "spectrum_smoothness": self.spec_smooth_slider.value(),
            "spectrum_sensitivity": self.spec_sens_slider.value(),
            "spectrum_pos": spec_pos,
            "color": [self.spectrum_color.red(), self.spectrum_color.green(), self.spectrum_color.blue()],
            "text": self.text_input.text(),
            "text_color": self.text_color.name(),
            "font": self.font_box.currentText(),
            "text_shadow": self.text_shadow_chk.isChecked(),
            "text_border_enabled": self.text_border_chk.isChecked(),
            "text_border_color": self.text_border_color.name(),
            "text_border_width": self.text_border_width.value(),
            "fontsize": self.font_size_slider.value(),
            "text_pos": text_pos,
            "logo": self.logo_path,
            "logo_size": self.logo_size_slider.value(),
            "logo_pos": self.logo_pos_box.currentText(),
            "progressbar_enabled": self.prog_chk.isChecked(),
            "progressbar_color": [self.prog_color.red(), self.prog_color.green(), self.prog_color.blue()],
            "progressbar_height": self.prog_height_slider.value(),
            "progressbar_pos": self.prog_pos_box.currentText(),
            "out": out_path
        }
        self.worker = Worker(config)
        self.worker.error.connect(self.handle_error)
        self.worker.progress.connect(self.pbar.setValue)
        self.worker.success.connect(self.handle_success)
        self.worker.start()
        self.render_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.open_folder_btn.setVisible(False)
        self.statusBar().showMessage("Rendering started...")

    def handle_success(self):
        self.statusBar().showMessage("Rendering Complete!", 5000)
        self.pbar.setValue(100)
        self.render_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.open_folder_btn.setVisible(True)

    def handle_error(self, err_msg):
        self.render_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        if "Render Cancelled" in err_msg:
            self.statusBar().showMessage("Render Cancelled", 5000)
            self.pbar.setValue(0)
        else:
            self.preview_area.setText(f"Render Error:\n{err_msg}\n\nTry switching 'Processor' to CPU.")
            print(f"Render Error: {err_msg}")
            self.statusBar().showMessage("Rendering Failed", 5000)

    def closeEvent(self, event):
        if hasattr(self, 'worker') and self.worker.isRunning():
            reply = QMessageBox.question(self, 'Render in Progress',
                                       "A render is currently in progress. Are you sure you want to exit?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if reply == QMessageBox.Yes:
                self.worker.requestInterruption()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #2b2b2b;
    color: #e0e0e0;
    font-family: 'Segoe UI', sans-serif;
    font-size: 14px;
}
QGroupBox {
    border: 1px solid #444;
    border-radius: 6px;
    margin-top: 12px;
    font-weight: bold;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    left: 10px;
    color: #3498db;
}
QPushButton {
    background-color: #3c3c3c;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 6px 12px;
    color: #fff;
}
QPushButton:hover {
    background-color: #4a4a4a;
    border-color: #666;
}
QPushButton:pressed {
    background-color: #252525;
}
QLineEdit, QSpinBox, QComboBox {
    background-color: #1e1e1e;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px;
    color: #fff;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #3498db;
}
QSlider::groove:horizontal {
    border: 1px solid #3a3a3a;
    height: 6px;
    background: #1e1e1e;
    margin: 2px 0;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #3498db;
    border: 1px solid #3498db;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QProgressBar {
    border: 1px solid #444;
    border-radius: 4px;
    text-align: center;
    background-color: #1e1e1e;
    color: white;
}
QProgressBar::chunk {
    background-color: #2ecc71;
    border-radius: 3px;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
QStatusBar {
    background-color: #2b2b2b;
    color: #e0e0e0;
    border-top: 1px solid #444;
}
"""

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    license_dialog = LicenseDialog()
    if license_dialog.exec():
        window = MainWindow(expiry_date_str=license_dialog.expiry_date_str)
        
        # Set custom application icon (place your 'logo.ico' or 'logo.png' file in the same folder)
        icon_path = 'logo.ico' 
        if os.path.exists(icon_path):
            window.setWindowIcon(QIcon(icon_path))
            
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit(0)