import numpy as np
import librosa
from moviepy.editor import VideoFileClip, ImageClip, AudioFileClip, VideoClip, CompositeVideoClip, afx
from moviepy.video.tools.subtitles import SubtitlesClip
from moviepy.editor import TextClip
from moviepy.config import change_settings

# Point this to the exact path where you installed ImageMagick
# Usually it's in C:\Program Files\ImageMagick-...
change_settings({"IMAGEMAGICK_BINARY": r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"})

def make_spectrum_frame(t, audio_data, sr, res_width, res_height, color):
    # FFT Analysis for spectrum
    idx = int(t * sr) % len(audio_data) # Use modulo to loop audio data for spectrum
    window = audio_data[idx : idx + 2048]
    if len(window) < 2048: window = np.pad(window, (0, 2048 - len(window)))
    fft_data = np.abs(np.fft.rfft(window))
    
    frame = np.zeros((res_height, res_width, 3), dtype=np.uint8)
    num_bars = 40
    bar_width = res_width // num_bars
    
    for i in range(num_bars):
        val = np.mean(fft_data[i*2 : (i+1)*2]) # Group frequencies
        h = int(min(val * 5, res_height / 2)) # Sensitivity scaling
        frame[res_height-h : res_height, i*bar_width : (i+1)*bar_width-2] = color
    return frame
def run_render(config, logger_cb):
    # Resolution Map
    res_map = {"720p": (1280, 720), "1080p": (1920, 1080), "2K": (2560, 1440), "4K": (3840, 2160)}
    w, h = res_map[config['res']]
    dur = config['duration'] * 60 # Convert min to sec

    # Load Media
    audio = AudioFileClip(config['audio'])
    looped_audio = afx.audio_loop(audio, duration=dur)
    
    if config['video'].lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        bg = VideoFileClip(config['video']).loop(duration=dur).resize(newsize=(w, h))
    else:
        bg = ImageClip(config['video']).set_duration(dur).resize(newsize=(w, h))

    # Spectrum logic
    audio_data, sr = librosa.load(config['audio'], sr=None)
    spec_clip = VideoClip(lambda t: make_spectrum_frame(t, audio_data, sr, w, h, config['color']), duration=dur)

    # Hardware Params
    gpu_map = {
        "GPU (Nvidia)": {"codec": "h264_nvenc", "params": ["-preset", "p6", "-cq", "20"]},
        "GPU (AMD)": {"codec": "h264_amf", "params": ["-quality", "quality"]},
        "CPU": {"codec": "libx264", "params": ["-crf", "18"]}
    }
    gpu = gpu_map[config['processor']]

    final = CompositeVideoClip([bg, spec_clip.set_position(('center', 'bottom'))]).set_audio(looped_audio)
    final.write_videofile(config['out'], fps=30, codec=gpu['codec'], ffmpeg_params=gpu['params'], logger=logger_cb)

def create_lyrics_clip(srt_path, video_w, video_h):
    """
    Parses SRT file and creates a synchronized text overlay.
    """
    generator = lambda txt: TextClip(
        txt, 
        font='Arial-Bold', 
        fontsize=video_h // 18, 
        color='white',
        stroke_color='black',
        stroke_width=1,
        method='caption',
        size=(video_w * 0.8, None)
    )
    
    subtitles = SubtitlesClip(srt_path, generator)
    # Position lyrics at the bottom 20% of the screen
    return subtitles.set_position(('center', video_h * 0.75))
# Updated Composite logic for engine.py
def assemble_final_video(bg_clip, spec_clip, srt_path, logo_path, w, h):
    layers = [bg_clip] # Bottom Layer
    
    # Add Spectrum
    layers.append(spec_clip.set_position(('center', 'bottom')))
    
    # Add Lyrics if SRT exists
    if srt_path:
        lyrics = create_lyrics_clip(srt_path, w, h)
        layers.append(lyrics)
        
    # Add Logo if path exists
    if logo_path:
        logo = (ImageClip(logo_path)
                .set_duration(bg_clip.duration)
                .resize(height=h // 10)
                .set_position(('right', 'top'))
                .margin(right=20, top=20, opacity=0))
        layers.append(logo)

    return CompositeVideoClip(layers)
def get_beat_zoom(t, audio_data, sr):
    """
    Calculates a zoom factor based on the audio volume at time t.
    """
    # Look at a 10th of a second window
    start_idx = int(t * sr)
    end_idx = int((t + 0.1) * sr)
    
    if end_idx > len(audio_data):
        return 1.0
        
    # Get the volume (RMS) of this segment
    segment = audio_data[start_idx:end_idx]
    volume = np.sqrt(np.mean(segment**2))
    
    # Scale: Base zoom 1.0 + volume-based pulse (max 5% zoom)
    pulse = 1.0 + (volume * 0.2)
    return min(pulse, 1.05)

# How to apply it in the render function:
# bg_clip = bg_clip.resize(lambda t: get_beat_zoom(t, audio_data, sr))
