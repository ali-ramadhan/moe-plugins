import moe

# This plugin returns "pretty" text formatting for the media + encoding, e.g.
#   [CD FLAC 16bit 44.1kHz]
#   [Vinyl FLAC 24bit 192kHz]
#   [WEB FLAC 16+24bit 48+96kHz]
#   [WEB MP3+FLAC 44.1+48kHz]

@moe.hookimpl
def create_path_template_func():
    return [media_encoding]

def album_audio_format(album):
    audio_formats = [track.audio_format for track in album.tracks]
    audio_formats = list(set(audio_formats))

    if len(audio_formats) == 1:
        return f"{audio_formats[0].upper()}"
    else:
        return "+".join(map(str, audio_formats))

def album_bit_depth(album):
    bit_depths = [track.bit_depth for track in album.tracks]
    bit_depths = list(set(bit_depths))

    if len(bit_depths) == 1:
        return f"{bit_depths[0]}bit"
    else:
        return "+".join(map(str, bit_depths)) + "bit"

def pretty_sample_rate(f_Hz):
    f_kHz = f_Hz / 1000
    if f_kHz.is_integer():
        return str(int(f_kHz))
    else:
        return str(f_kHz)

def album_sample_rate(album):
    sample_rates = [track.sample_rate for track in album.tracks]
    sample_rates = list(set(sample_rates))

    if len(sample_rates) == 1:
        return f"{pretty_sample_rate(sample_rates[0])}kHz"
    else:
        return "+".join(map(pretty_sample_rate, bit_depths)) + "kHz"

def media_encoding(album):
    media = album.media
    audio_format = album_audio_format(album)
    bit_depth = album_bit_depth(album)
    sample_rate = album_sample_rate(album)
    return f"{media} {audio_format} {bit_depth} {sample_rate}"
