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
from moviepy.video.tools.subtitles import SubtitlesClip, file_to_subtitles
from PIL import Image, ImageDraw, ImageFont
from functools import lru_cache

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
    base_res_map = {"720p": 720, "1080p": 1080, "2K": 1440, "4K": 2160}
    base_h = base_res_map.get(config['res'], 1080)
    
    ar_str = config.get('aspect_ratio', '16:9')
    ar_w, ar_h = map(int, ar_str.split(':'))
    ratio = ar_w / ar_h
    
    if ratio >= 1:
        h = base_h
        w = int(h * ratio)
    else:
        w = base_h
        h = int(w / ratio)
    
    if w % 2 != 0: w += 1
    if h % 2 != 0: h += 1

    dur = config['duration'] # Duration is passed in seconds
    fps = 30

    # Load Media
    audio_paths = config['audio']
    if isinstance(audio_paths, str):
        audio_paths = [audio_paths]

    audio_clips = [AudioFileClip(p) for p in audio_paths]
    audio_durations = [c.duration for c in audio_clips]
    if len(audio_clips) > 1:
        audio = concatenate_audioclips(audio_clips)
    else:
        audio = audio_clips[0]
        
    looped_audio = afx.audio_loop(audio, duration=dur)
    
    if config['video'].lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        bg = VideoFileClip(config['video']).loop(duration=dur)
    else:
        bg = ImageClip(config['video']).set_duration(dur)

    # Crop to Fill Logic
    bg_w, bg_h = bg.size
    bg_ratio = bg_w / bg_h
    target_ratio = w / h
    
    if bg_ratio > target_ratio:
        new_w = int(bg_h * target_ratio)
        bg = bg.crop(x1=(bg_w - new_w) // 2, width=new_w)
    else:
        new_h = int(bg_w / target_ratio)
        bg = bg.crop(y1=(bg_h - new_h) // 2, height=new_h)

    bg = bg.resize(newsize=(w, h)).set_fps(30)

    clips = [bg]
    
    # Pre-load audio data if needed for spectrum OR beat shake
    audio_data = None
    sr = 44100
    if config.get('spectrum', False) or config.get('lyrics_bounce', False):
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

        # Cache for spectrum frames to avoid double rendering (RGB + Mask)
        _spec_cache = {}

        def make_spectrum_rgba(t):
            frame_idx = int(t * fps) % bar_heights.shape[1]
            if frame_idx in _spec_cache:
                return _spec_cache[frame_idx]

            # Initialize RGBA frame (transparent black)
            frame = np.zeros((h, w, 4), dtype=np.uint8)
            color_rgba = tuple(config['color']) + (255,)
            
            if style in ["Circle", "Line", "Filled Line"]:
                pil_img = Image.new('RGBA', (w, h), (0,0,0,0))
                draw = ImageDraw.Draw(pil_img)
                cx = start_x + spec_width // 2
                cy = base_y
                radius = 100 * (config.get('spectrum_size', 50) / 50.0)
                color_tuple = tuple(config['color'])
                
                if style == "Circle":
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
                            draw.line([(sx, sy), (ex, ey)], fill=color_rgba, width=drawn_w)
                
                elif style == "Line":
                    points = []
                    for i in range(num_bars):
                        bh = int(bar_heights[i, frame_idx])
                        bx = start_x + i*bar_width + offset
                        cx = bx + drawn_w // 2
                        y = base_y + bh if is_top else base_y - bh
                        points.append((cx, y))
                    if len(points) > 1:
                        draw.line(points, fill=color_rgba, width=drawn_w)

                elif style == "Filled Line":
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
                        draw.polygon(points, fill=color_rgba)
                
                frame = np.array(pil_img)

            else:
                # Numpy based styles
                color_arr = list(config['color']) + [255]
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
                            bx = start_x + i*bar_width + offset
                            bx_end = bx + drawn_w
                            y1, y2 = 0, 0
                        if style == "Mirrored":
                            y1 = max(0, base_y - bh)
                            y2 = min(h, base_y + bh)
                        elif style == "Dots":
                            y1 = max(0, base_y - bh)
                            y2 = min(h, y1 + 4)
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
                                frame[y1:y2, bx:bx_end] = color_arr
                            continue # Skip default assignment
                        else: # Bars
                            if is_top:
                                y1 = base_y + b
                                y2 = min(h, y1 + block_h)
                                y1 = base_y
                                y2 = min(h, base_y + bh)
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
                        if y2 > y1:
                            frame[y1:y2, bx:bx_end] = color_arr

            # Cache management
            if len(_spec_cache) > 60: _spec_cache.clear()
            _spec_cache[frame_idx] = frame
            return frame

        def make_frame(t):
            return make_spectrum_rgba(t)[:,:,:3]

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

    if config.get('lyrics_file') and os.path.exists(config['lyrics_file']):
        l_path = config['lyrics_file']
        l_font = config.get('lyrics_font', 'Arial')
        l_fontsize = config.get('lyrics_fontsize', 50)
        l_color = config.get('lyrics_color', 'white')
        
        # Background Dim Logic
        if config.get('lyrics_bg_dim'):
            dim_clip = ColorClip(size=(w, h), color=(0,0,0)).set_opacity(0.4).set_duration(dur)
            clips.insert(1, dim_clip) # Insert after BG, before other overlays
        
        def generator(txt):
            tc = TextClip(txt, font=l_font, fontsize=l_fontsize, color=l_color,
                           stroke_color='black', stroke_width=2, print_cmd=False)
            if tc.w > w * 0.9:
                tc = tc.resize(width=int(w * 0.9))
            
            return tc
        
        subs = []
        if l_path.lower().endswith('.srt'):
            try:
                subs = file_to_subtitles(l_path)
            except Exception as e:
                print(f"SRT Parse Error: {e}")
        elif l_path.lower().endswith('.lrc'):
            try:
                with open(l_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                parsed = []
                for line in lines:
                    line = line.strip()
                    if line.startswith('[') and ']' in line:
                        idx = line.find(']')
                        time_str = line[1:idx]
                        content = line[idx+1:].strip()
                        parts = time_str.split(':')
                        if len(parts) == 2:
                            sec = float(parts[0])*60 + float(parts[1])
                            parsed.append((sec, content))
                for i in range(len(parsed)):
                    s, t = parsed[i]
                    if not t: continue
                    e = parsed[i+1][0] if i < len(parsed)-1 else dur
                    e = min(e, dur)
                    if s < dur:
                        subs.append(((s, e), t))
            except Exception as e:
                print(f"LRC Parse Error: {e}")

        if subs:
            # Global Fixed Background Box Logic
            if config.get('lyrics_box_enabled'):
                l_pos = config.get('lyrics_pos', 'Bottom')
                if isinstance(l_pos, (list, tuple)):
                    cy = int(l_pos[1] * h)
                elif l_pos == "Top": cy = int(h * 0.1)
                elif l_pos == "Center": cy = h // 2
                else: cy = int(h * 0.8)

                box_c = config.get('lyrics_box_color', [0,0,0,128])
                rgb = tuple(box_c[:3])
                opacity = box_c[3] / 255.0
                
                box_w = int(w * 0.9)
                box_h = int(l_fontsize * 1.5 * 2) # 2 lines height
                
                bg_box = ColorClip(size=(box_w, box_h), color=rgb).set_opacity(opacity)
                bg_box = bg_box.set_pos(('center', cy)).set_duration(dur)
                clips.insert(2, bg_box) # Insert after BG/Dim, before text

            if config.get('lyrics_scrolling', False):
                # Smooth Scrolling Teleprompter Mode
                l_pos = config.get('lyrics_pos', 'Bottom')
                
                # Determine center Y
                if isinstance(l_pos, (list, tuple)):
                    cy = int(l_pos[1] * h)
                elif l_pos == "Top": cy = int(h * 0.1)
                elif l_pos == "Center": cy = h // 2
                else: cy = int(h * 0.8)

                line_spacing = int(l_fontsize * 1.5)
                
                # Pre-calculate timings for virtual index
                timings = []
                for i in range(len(subs)):
                    s, e = subs[i][0]
                    next_s = subs[i+1][0][0] if i < len(subs)-1 else e + 5.0
                    timings.append((s, e, next_s))

                def get_v_idx(t):
                    for i, (s, e, next_s) in enumerate(timings):
                        if s <= t <= e:
                            # Scroll continuously during the line
                            return i + (t - s) / (e - s)
                        if e < t < next_s:
                            # During gap, hold position at the start of the next line
                            return float(i + 1)
                    if timings and t >= timings[-1][1]: return float(len(timings))
                    return -1.0

                scroll_clips = []
                for i, ((s, e), txt) in enumerate(subs):
                    tc_main = TextClip(txt, font=l_font, fontsize=l_fontsize, color=l_color, 
                                stroke_color='black', stroke_width=2, print_cmd=False)
                    
                    if tc_main.w > w * 0.9:
                        tc_main = tc_main.resize(width=int(w * 0.9))
                    
                    def make_pos(t):
                        v = get_v_idx(t)
                        y = cy + ((i - v) * line_spacing)
                        return ('center', int(y))

                    def fade_filter(get_frame, t):
                        frame = get_frame(t).copy()
                        v = get_v_idx(t)
                        opacity = max(0.0, 1.0 - abs(i - v))
                        if opacity < 1.0:
                            frame[:,:,3] = (frame[:,:,3] * opacity).astype(np.uint8)
                        return frame

                    tc = tc_main.set_position(make_pos).set_duration(dur).fl(fade_filter)
                    scroll_clips.append(tc)
                
                clips.extend(scroll_clips)

            elif config.get('lyrics_karaoke', False):
                # Karaoke Mode: Generate individual clips with wipe effect
                l_pos = config.get('lyrics_pos', 'Bottom')
                
                # Determine position tuple once
                if isinstance(l_pos, (list, tuple)):
                    final_pos = ('center', int(l_pos[1] * h))
                elif l_pos == "Top": final_pos = ('center', int(h * 0.1))
                elif l_pos == "Center": final_pos = ('center', 'center')
                else: final_pos = ('center', int(h * 0.8))

                karaoke_clips = []
                for (start, end), txt in subs:
                    dur_chunk = end - start
                    if dur_chunk <= 0: continue

                    # Base Text (Inactive - Gray)
                    txt_base = TextClip(txt, font=l_font, fontsize=l_fontsize, color='gray', 
                                      stroke_color='black', stroke_width=2, print_cmd=False)
                    
                    # Active Text (Active Color) - Past Words
                    txt_active = TextClip(txt, font=l_font, fontsize=l_fontsize, color=l_color, 
                                        stroke_color='black', stroke_width=2, print_cmd=False)
                    
                    # Highlight Text (Current Word) - Yellow
                    txt_highlight = TextClip(txt, font=l_font, fontsize=l_fontsize, color='yellow', 
                                        stroke_color='black', stroke_width=2, print_cmd=False)
                    
                    # Resize if too wide
                    if txt_base.w > w * 0.9:
                        txt_base = txt_base.resize(width=int(w * 0.9))
                        txt_active = txt_active.resize(width=int(w * 0.9))
                        txt_highlight = txt_highlight.resize(width=int(w * 0.9))

                    # Word-by-word Karaoke Effect
                    txt_w = txt_active.w
                    words = txt.split()
                    
                    # Analyze image to find word boundaries (gaps in alpha channel)
                    boundaries = []
                    try:
                        fr = txt_active.get_frame(0)
                        alpha = fr[:, :, 3]
                        col_sum = np.sum(alpha, axis=0)
                        has_ink = col_sum > 0
                        is_ink = False
                        for x, val in enumerate(has_ink):
                            if val: is_ink = True
                            elif is_ink:
                                boundaries.append(x)
                                is_ink = False
                        if is_ink: boundaries.append(txt_w)
                    except: boundaries = []

                    # Map time to words based on character count
                    # Use sum of weights to ensure 0-1 coverage
                    total_weight = sum(len(w) + 1 for w in words)
                    if total_weight == 0: total_weight = 1
                    
                    word_timings = []
                    curr_char = 0
                    for w in words:
                        w_len = len(w) + 1 # Include space
                        start_p = curr_char / total_weight
                        end_p = (curr_char + w_len) / total_weight
                        word_timings.append((start_p, end_p))
                        curr_char += w_len

                    def past_mask_wipe(get_mask, t):
                        m = get_mask(t).copy()
                        prog = t / dur_chunk
                        
                        idx = 0
                        for i, (s, e) in enumerate(word_timings):
                            if prog >= s: idx = i
                        
                        if idx == 0:
                            m[:] = 0
                        else:
                            limit_x = boundaries[idx-1] if len(boundaries) == len(words) else int(txt_w * word_timings[idx-1][1])
                            m[:, limit_x:] = 0
                        return m

                    def current_mask_wipe(get_mask, t):
                        m = get_mask(t).copy()
                        prog = t / dur_chunk
                        
                        idx = 0
                        for i, (s, e) in enumerate(word_timings):
                            if prog >= s: idx = i
                        
                        start_x = 0
                        if idx > 0:
                            start_x = boundaries[idx-1] if len(boundaries) == len(words) else int(txt_w * word_timings[idx-1][1])
                        
                        end_x = boundaries[idx] if len(boundaries) == len(words) else int(txt_w * word_timings[idx][1])
                        
                        m[:, :start_x] = 0
                        m[:, end_x:] = 0
                        return m

                    if txt_active.mask:
                        txt_active = txt_active.set_duration(dur_chunk).set_mask(txt_active.mask.fl(past_mask_wipe))
                    
                    if txt_highlight.mask:
                        txt_highlight = txt_highlight.set_duration(dur_chunk).set_mask(txt_highlight.mask.fl(current_mask_wipe))
                        
                    txt_base = txt_base.set_duration(dur_chunk)

                    # Composite Base + Active + Highlight
                    layers = [txt_base, txt_active, txt_highlight]
                    comp = CompositeVideoClip(layers, size=txt_base.size)
                    comp = comp.set_pos(final_pos).set_start(start)
                    karaoke_clips.append(comp)
                
                clips.extend(karaoke_clips)
            
            elif config.get('lyrics_bounce', False) and audio_data is not None:
                lyrics_clip = SubtitlesClip(subs, generator)
                hop_length = int(sr / fps)
                rms = librosa.feature.rms(y=audio_data, frame_length=2048, hop_length=hop_length)[0]
                rms = rms / (np.max(rms) + 1e-6)
                
                def get_scale(t):
                    frame_idx = int(t * fps)
                    if frame_idx < len(rms):
                        return 1.0 + (rms[frame_idx] * 0.3)
                    return 1.0

                l_pos = config.get('lyrics_pos', 'Bottom')
                cx = w // 2
                cy = int(h * 0.8)
                if isinstance(l_pos, (list, tuple)):
                    cx = int(l_pos[0] * w)
                    cy = int(l_pos[1] * h)
                elif l_pos == "Top": cy = int(h * 0.1)
                elif l_pos == "Center": cy = h // 2
                
                @lru_cache(maxsize=10)
                def get_bounced_image(t):
                    im = lyrics_clip.make_frame(t)
                    mk = lyrics_clip.mask.make_frame(t)
                    scale = get_scale(t)
                    pil_im = Image.fromarray(im)
                    pil_mk = Image.fromarray((mk * 255).astype('uint8'), mode='L')
                    pil_im.putalpha(pil_mk)
                    if scale > 1.01:
                        nw = int(pil_im.width * scale)
                        nh = int(pil_im.height * scale)
                        pil_im = pil_im.resize((nw, nh), Image.LANCZOS)
                    bg = Image.new('RGBA', (w, h), (0,0,0,0))
                    bg.paste(pil_im, (cx - pil_im.width // 2, cy - pil_im.height // 2), pil_im)
                    return bg

                clips.append(VideoClip(lambda t: np.array(get_bounced_image(t))[:,:,:3], duration=dur)
                             .set_mask(VideoClip(lambda t: np.array(get_bounced_image(t))[:,:,3] / 255.0, duration=dur, ismask=True)))
            else:
                lyrics_clip = SubtitlesClip(subs, generator)
                l_pos = config.get('lyrics_pos', 'Bottom')
                if isinstance(l_pos, (list, tuple)):
                    lyrics_clip = lyrics_clip.set_pos(('center', int(l_pos[1] * h)))
                elif l_pos == "Top": lyrics_clip = lyrics_clip.set_pos(('center', int(h * 0.1)))
                elif l_pos == "Center": lyrics_clip = lyrics_clip.set_pos('center')
                else: lyrics_clip = lyrics_clip.set_pos(('center', int(h * 0.8)))
                clips.append(lyrics_clip.set_duration(dur))

    # Hardware Params
    gpu_map = {
        "GPU (Nvidia)": {"codec": "h264_nvenc", "params": ["-preset", "p3", "-tune", "hq", "-b:v", "5M"]},
        "GPU (AMD)": {"codec": "h264_amf", "params": ["-quality", "speed"]},
        "CPU": {"codec": "libx264", "params": ["-preset", "veryfast", "-crf", "20"]}
    }
    gpu = gpu_map[config['processor']]

    final = CompositeVideoClip(clips)

    final = final.set_audio(looped_audio)
    final.write_videofile(config['out'], fps=fps, codec=gpu['codec'], ffmpeg_params=gpu['params'], 
                          logger=logger_cb, threads=os.cpu_count())
