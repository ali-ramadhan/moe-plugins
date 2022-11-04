import moe

@moe.hookimpl
def create_path_template_func():
    return [disc_dir_padded]

def disc_dir_padded(track, album):
    if album.disc_total == 1:
        return ""
    elif 2 <= album.disc_total <= 9:
        return f"Disc {track.disc:1d}"
    elif 10 <= album.disc_total <= 99:
        return f"Disc {track.disc:02d}"
    elif 101 <= album.disc_total <= 999:
        return f"Disc {track.disc:03d}"
