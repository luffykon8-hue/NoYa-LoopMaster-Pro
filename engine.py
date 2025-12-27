import os
import shutil
import math
import random
import numpy as np
import librosa
from proglog import ProgressBarLogger
from moviepy.editor import VideoFileClip, ImageClip, AudioFileClip, VideoClip, CompositeVideoClip, afx, concatenate_audioclips, ColorClip
from moviepy.editor import TextClip
from moviepy.config import change_settings
from PIL import Image, ImageDraw, ImageFont

# Robust ImageMagick Configuration
def configure_imagemagick():
    # 1. Check the specific path you had (if it exists, use it)
    specific_path = r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"
    if os.path.exists(specific_path):
        change_settings({"IMAGEMAGICK_BINARY": specific_path})
        return

    # 2. Otherwise, try to find 'magick' in the system PATH
    system_magick = shutil.which("magick")
    if system_magick:
        change_settings({"IMAGEMAGICK_BINARY": system_magick})

configure_imagemagick()

class RenderLogger(ProgressBarLogger):
    def __init__(self, callback, cancel_check=None):
        super().__init__()
        self.progress_cb = callback
        self.cancel_check = cancel_check
    def bars_callback(self, bar, attr, value, old_value=None):
        if self.cancel_check and self.cancel_check():
            raise Exception("Render Cancelled")
        if bar == 't':
            total = self.bars[bar]['total']
            if total > 0:
                self.progress_cb(int((value / total) * 100))

def run_render(config, logger_cb):
    # Resolution Map
    res_map = {"720p": (1280, 720), "1080p": (1920, 1080), "2K": (2560, 1440), "4K": (3840, 2160)}
    w, h = res_map[config['res']]
    dur = config['duration'] # Duration is passed in seconds
    fps = 30

    # Load Media
    audio_paths = config['audio']
    if isinstance(audio_paths, str):
        audio_paths = [audio_paths]

    audio_clips = [AudioFileClip(p) for p in audio_paths]
    if len(audio_clips) > 1:
        audio = concatenate_audioclips(audio_clips)
    else:
        audio = audio_clips[0]
        
    looped_audio = afx.audio_loop(audio, duration=dur)
    
    if config['video'].lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        bg = VideoFileClip(config['video']).loop(duration=dur).resize(newsize=(w, h)).set_fps(30)
    else:
        bg = ImageClip(config['video']).set_duration(dur).resize(newsize=(w, h)).set_fps(30)

    clips = [bg]
    
    # Pre-load audio data if needed for spectrum OR beat shake
    audio_data = None
    sr = 44100
    if config.get('spectrum', False):
        audio_segments = []
        for p in audio_paths:
            y, _ = librosa.load(p, sr=sr)
            audio_segments.append(y)
        if audio_segments:
            audio_data = np.concatenate(audio_segments)

    if config.get('spectrum', False) and audio_data is not None:
        # fps defined above
        hop_length = int(sr / fps)
        stft = np.abs(librosa.stft(audio_data, n_fft=2048, hop_length=hop_length))
        
        num_bars = 50 
        bar_width = w // num_bars
        # Focus on frequencies up to 3kHz for better visual response
        relevant_bins = int(3000 / (sr / 2048)) 
        bins_per_bar = max(1, relevant_bins // num_bars)
        
        bar_heights_list = []
        for i in range(num_bars):
            start_bin = i * bins_per_bar
            end_bin = (i + 1) * bins_per_bar
            bar_heights_list.append(np.mean(stft[start_bin:end_bin, :], axis=0))
        
        bar_heights = np.array(bar_heights_list)
        
        # Sensitivity
        sensitivity = config.get('spectrum_sensitivity', 100) / 100.0
        bar_heights = bar_heights * sensitivity
        
        # Smoothing
        smoothness = config.get('spectrum_smoothness', 0)
        if smoothness > 0:
            alpha = 1 - (smoothness / 100.0)
            smoothed = np.zeros_like(bar_heights)
            smoothed[:, 0] = bar_heights[:, 0]
            for t in range(1, bar_heights.shape[1]):
                smoothed[:, t] = alpha * bar_heights[:, t] + (1 - alpha) * smoothed[:, t-1]
            bar_heights = smoothed
        
        # Scale based on user input (1-150%)
        # Base scale factor * user slider
        scale_factor = 6 * (config.get('spectrum_size', 50) / 50.0)
        bar_heights = np.clip(bar_heights * scale_factor, 0, h // 2)
        
        # Position Logic
        spec_pos = config.get('spectrum_pos', 'Bottom')
        if isinstance(spec_pos, (list, tuple)):
            # Custom (rx, ry)
            base_y = int(spec_pos[1] * h)
        elif spec_pos == "Top": base_y = int(h * 0.05)
        elif spec_pos == "Center": base_y = h // 2
        else: base_y = int(h * 0.95) # Bottom
        
        is_top = False
        if spec_pos == "Top": is_top = True
        elif isinstance(spec_pos, (list, tuple)) and spec_pos[1] < 0.4: is_top = True
        
        # Center X logic
        spec_width = int(w * 0.8)
        start_x = (w - spec_width) // 2
        if isinstance(spec_pos, (list, tuple)):
            start_x = int(spec_pos[0] * w - (spec_width / 2))
            
        # Recalculate bar width for render
        bar_width = spec_width // num_bars
        style = config.get('spectrum_style', 'Bars')
        
        # Thickness logic (percentage of bar_width)
        thickness_pct = config.get('spectrum_thickness', 80) / 100.0
        drawn_w = max(1, int(bar_width * thickness_pct))
        offset = (bar_width - drawn_w) // 2
        
        def make_frame(t):
            frame_idx = int(t * fps) % bar_heights.shape[1]
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            
            if style == "Circle":
                pil_img = Image.new('RGB', (w, h), (0,0,0))
                draw = ImageDraw.Draw(pil_img)
                cx = start_x + spec_width // 2
                cy = base_y
                radius = 100 * (config.get('spectrum_size', 50) / 50.0)
                color_tuple = tuple(config['color'])
                
                for i in range(num_bars):
                    bh = int(bar_heights[i, frame_idx])
                    if bh > 0:
                        angle = (i / num_bars) * 2 * math.pi
                        # Start point (on radius)
                        sx = cx + radius * math.cos(angle - math.pi/2)
                        sy = cy + radius * math.sin(angle - math.pi/2)
                        # End point (outwards)
                        ex = cx + (radius + bh) * math.cos(angle - math.pi/2)
                        ey = cy + (radius + bh) * math.sin(angle - math.pi/2)
                        draw.line([(sx, sy), (ex, ey)], fill=color_tuple, width=drawn_w)
                return np.array(pil_img)
            
            if style == "Line":
                pil_img = Image.new('RGB', (w, h), (0,0,0))
                draw = ImageDraw.Draw(pil_img)
                points = []
                for i in range(num_bars):
                    bh = int(bar_heights[i, frame_idx])
                    bx = start_x + i*bar_width + offset
                    cx = bx + drawn_w // 2
                    y = base_y + bh if is_top else base_y - bh
                    points.append((cx, y))
                
                if len(points) > 1:
                    draw.line(points, fill=tuple(config['color']), width=drawn_w)
                return np.array(pil_img)
            
            if style == "Filled Line":
                pil_img = Image.new('RGB', (w, h), (0,0,0))
                draw = ImageDraw.Draw(pil_img)
                points = []
                for i in range(num_bars):
                    bh = int(bar_heights[i, frame_idx])
                    bx = start_x + i*bar_width + offset
                    cx = bx + drawn_w // 2
                    y = base_y + bh if is_top else base_y - bh
                    points.append((cx, y))
                
                if points:
                    # Close polygon
                    points.append((points[-1][0], base_y))
                    points.append((points[0][0], base_y))
                    draw.polygon(points, fill=tuple(config['color']))
                return np.array(pil_img)

            for i in range(num_bars):
                bh = int(bar_heights[i, frame_idx])
                if bh > 0:
                    bx = start_x + i*bar_width + offset
                    bx_end = bx + drawn_w
                    
                    if style == "Mirrored":
                        # Grow Up and Down
                        y1 = max(0, base_y - bh)
                        y2 = min(h, base_y + bh)
                        frame[y1:y2, bx:bx_end] = config['color']
                    elif style == "Dots":
                        # Just top
                        y1 = max(0, base_y - bh)
                        y2 = min(h, y1 + 4)
                        frame[y1:y2, bx:bx_end] = config['color']
                    elif style == "Blocks":
                        block_h = max(2, int(h * 0.01)) # 1% of screen height
                        gap = max(1, int(block_h * 0.5))
                        for b in range(0, bh, block_h + gap):
                            if is_top:
                                y1 = base_y + b
                                y2 = min(h, y1 + block_h)
                            else:
                                y2 = base_y - b
                                y1 = max(0, y2 - block_h)
                            frame[y1:y2, bx:bx_end] = config['color']
                    else:
                        # Bars
                        if is_top:
                            # Grow Down
                            y1 = base_y
                            y2 = min(h, base_y + bh)
                        else:
                            # Grow Up
                            y1 = max(0, base_y - bh)
                            y2 = min(h, base_y)
                        
                        frame[y1:y2, bx:bx_end] = config['color']
            return frame

        def make_mask(t):
            frame_idx = int(t * fps) % bar_heights.shape[1]
            mask = np.zeros((h, w), dtype=float)
            
            if style == "Circle":
                pil_img = Image.new('L', (w, h), 0)
                draw = ImageDraw.Draw(pil_img)
                cx = start_x + spec_width // 2
                cy = base_y
                radius = 100 * (config.get('spectrum_size', 50) / 50.0)
                
                for i in range(num_bars):
                    bh = int(bar_heights[i, frame_idx])
                    if bh > 0:
                        angle = (i / num_bars) * 2 * math.pi
                        sx = cx + radius * math.cos(angle - math.pi/2)
                        sy = cy + radius * math.sin(angle - math.pi/2)
                        ex = cx + (radius + bh) * math.cos(angle - math.pi/2)
                        ey = cy + (radius + bh) * math.sin(angle - math.pi/2)
                        draw.line([(sx, sy), (ex, ey)], fill=255, width=drawn_w)
                return np.array(pil_img) / 255.0
            
            if style == "Line":
                pil_img = Image.new('L', (w, h), 0)
                draw = ImageDraw.Draw(pil_img)
                points = []
                for i in range(num_bars):
                    bh = int(bar_heights[i, frame_idx])
                    bx = start_x + i*bar_width + offset
                    cx = bx + drawn_w // 2
                    y = base_y + bh if is_top else base_y - bh
                    points.append((cx, y))
                
                if len(points) > 1:
                    draw.line(points, fill=255, width=drawn_w)
                return np.array(pil_img) / 255.0
            
            if style == "Filled Line":
                pil_img = Image.new('L', (w, h), 0)
                draw = ImageDraw.Draw(pil_img)
                points = []
                for i in range(num_bars):
                    bh = int(bar_heights[i, frame_idx])
                    bx = start_x + i*bar_width + offset
                    cx = bx + drawn_w // 2
                    y = base_y + bh if is_top else base_y - bh
                    points.append((cx, y))
                
                if points:
                    points.append((points[-1][0], base_y))
                    points.append((points[0][0], base_y))
                    draw.polygon(points, fill=255)
                return np.array(pil_img) / 255.0

            for i in range(num_bars):
                bh = int(bar_heights[i, frame_idx])
                if bh > 0:
                    bx = start_x + i*bar_width + offset
                    bx_end = bx + drawn_w
                    
                    if style == "Mirrored":
                        y1 = max(0, base_y - bh)
                        y2 = min(h, base_y + bh)
                        mask[y1:y2, bx:bx_end] = 1.0
                    elif style == "Dots":
                        y1 = max(0, base_y - bh)
                        y2 = min(h, y1 + 4)
                        mask[y1:y2, bx:bx_end] = 1.0
                    elif style == "Blocks":
                        block_h = max(2, int(h * 0.01))
                        gap = max(1, int(block_h * 0.5))
                        for b in range(0, bh, block_h + gap):
                            if is_top:
                                y1 = base_y + b
                                y2 = min(h, y1 + block_h)
                            else:
                                y2 = base_y - b
                                y1 = max(0, y2 - block_h)
                            mask[y1:y2, bx:bx_end] = 1.0
                    else:
                        if is_top:
                            y1 = base_y
                            y2 = min(h, base_y + bh)
                        else:
                            y1 = max(0, base_y - bh)
                            y2 = min(h, base_y)
                        mask[y1:y2, bx:bx_end] = 1.0
            return mask

        spec_clip = VideoClip(make_frame, duration=dur).set_fps(fps)
        spec_mask = VideoClip(make_mask, duration=dur, ismask=True).set_fps(fps)
        spec_clip = spec_clip.set_mask(spec_mask)
        clips.append(spec_clip)

    if config.get('logo'):
        logo_clip = ImageClip(config['logo'])
        # Resize logo based on percentage of video height
        # config['logo_size'] is 1-100
        target_h = h * config.get('logo_size', 15) / 100
        logo_clip = logo_clip.resize(height=target_h)
        
        # Position Logic
        pos_str = config.get('logo_pos', 'Top Right')
        margin = int(h * 0.02) # 2% margin
        
        # Resolve X
        if "Left" in pos_str: pos_x = margin
        elif "Right" in pos_str: pos_x = w - logo_clip.w - margin
        else: pos_x = (w - logo_clip.w) // 2
        
        # Resolve Y
        if "Top" in pos_str: pos_y = margin
        elif "Bottom" in pos_str: pos_y = h - logo_clip.h - margin
        else: pos_y = (h - logo_clip.h) // 2
        
        logo_clip = logo_clip.set_pos((pos_x, pos_y)).set_duration(dur)
        clips.append(logo_clip)

    if config.get('progressbar_enabled'):
        bar_h_pct = config.get('progressbar_height', 2) / 100.0
        bar_h = int(h * bar_h_pct)
        bar_color = tuple(config.get('progressbar_color', [46, 204, 113]))
        
        # Create the full bar
        bar_clip = ColorClip((w, bar_h), color=bar_color).set_duration(dur)
        
        # Create a dynamic mask that reveals the bar over time
        def progress_mask(t):
            m = np.zeros((bar_h, w), dtype=float)
            progress = min(1.0, t / dur)
            current_w = int(w * progress)
            m[:, :current_w] = 1.0
            return m
            
        mask_clip = VideoClip(progress_mask, duration=dur, ismask=True)
        bar_clip = bar_clip.set_mask(mask_clip)
        
        pos_y = 0 if config.get('progressbar_pos') == 'Top' else h - bar_h
        bar_clip = bar_clip.set_pos((0, pos_y))
        clips.append(bar_clip)

    if config.get('text'):
        try:
            stroke_color = config.get('text_border_color') if config.get('text_border_enabled') else None
            stroke_width = config.get('text_border_width', 0) if config.get('text_border_enabled') else 0
            
            txt_clip = TextClip(config['text'], fontsize=config.get('fontsize', 70), 
                                color=config.get('text_color', 'white'), font=config.get('font', 'Arial'),
                                stroke_color=stroke_color, stroke_width=stroke_width)
            
            if config.get('text_shadow'):
                # Create a shadow using a black ColorClip masked by the text
                shadow = ColorClip(txt_clip.size, color=(0,0,0)).set_mask(txt_clip.mask).set_opacity(0.7)
                off = max(1, int(config.get('fontsize', 70) * 0.05))
                
                # Composite shadow and text
                txt_clip = CompositeVideoClip([
                    shadow.set_pos((off, off)),
                    txt_clip.set_pos((0, 0))
                ], size=(txt_clip.w + off, txt_clip.h + off))
                
        except Exception as e:
            raise Exception(f"TextClip Error (ImageMagick might be missing): {e}")
        
        text_pos = config.get('text_pos', 'Center')
        
        if isinstance(text_pos, (list, tuple)):
            # Custom relative position (x, y) 0.0-1.0
            rx, ry = text_pos
            tc_w, tc_h = txt_clip.size
            def pos_func(t):
                return (int(rx*w - tc_w/2), int(ry*h - tc_h/2))
            txt_clip = txt_clip.set_pos(pos_func).set_duration(dur)
        else:
            pos_map = {
                "Center": "center",
                "Top": ("center", "top"),
                "Bottom": ("center", "bottom")
            }
            txt_clip = txt_clip.set_pos(pos_map.get(text_pos, 'center')).set_duration(dur)
            
        clips.append(txt_clip)

    # Hardware Params
    gpu_map = {
        "GPU (Nvidia)": {"codec": "h264_nvenc", "params": ["-preset", "p4", "-tune", "hq", "-b:v", "5M"]},
        "GPU (AMD)": {"codec": "h264_amf", "params": ["-quality", "quality"]},
        "CPU": {"codec": "libx264", "params": ["-preset", "medium", "-crf", "18"]}
    }
    gpu = gpu_map[config['processor']]

    final = CompositeVideoClip(clips)

    final = final.set_audio(looped_audio)
    final.write_videofile(config['out'], fps=fps, codec=gpu['codec'], ffmpeg_params=gpu['params'], 
                          logger=logger_cb, threads=os.cpu_count())
