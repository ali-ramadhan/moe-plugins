import moe

@moe.hookimpl
def create_path_template_func():
    return [monolith_album_path]

# bucket plugin

def bucket(artist):
    first_char = artist[0]
    if first_char.isalpha():
        return first_char.upper()
    elif first_char.isnumeric():
        return "0-9"
    else:
        return "#-!"

# the plugin

articles = ["the", "a", "an"]

def the(name):
    name_split = name.split()
    first_word = name_split[0]
    if first_word.lower() in articles:
        name_no_article = " ".join(name_split[1:])
        return f"{name_no_article}, {first_word}"
    else:
        return name

# year_reissue plugin

def year_reissue(album):
    if album.year == album.original_year:
        return f"{album.year}"
    else:
        return f"{album.original_year}, RE-{album.year}"

# label_catalognum plugin

def label_catalognum(album):
    if album.label and album.catalog_num:
        return f" {{{album.label} {album.catalog_num}}}"
    elif album.label:
        return f" {{{album.label}}}"
    elif album.catalog_num:
        return f" {{{album.catalog_num}}}"
    else:
        return ""

# media_encoding plugin

media_subs = {
    "digital media": "WEB"
}

def standardized_album_media(album):
    if album.media and album.media.lower() in media_subs:
        return media_subs[album.media.lower()]
    else:
        return album.media

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
    media = standardized_album_media(album)
    audio_format = album_audio_format(album)
    bit_depth = album_bit_depth(album)
    sample_rate = album_sample_rate(album)
    return f"{media} {audio_format} {bit_depth} {sample_rate}"

# "monolith" plugin

def monolith_album_path(album):
    return f"{bucket(the(album.artist))}/{the(album.artist)}/{album.artist} - {album.title} ({year_reissue(album)}){label_catalognum(album)} [{media_encoding(album)}]"
