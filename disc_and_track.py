import moe

@moe.hookimpl
def create_path_template_func():
    return [disc_dir_padded, disc_and_track_num]

def disc_dir_padded(track, album):
    if album.disc_total == 1:
        return ""
    elif 2 <= album.disc_total <= 9:
        return f"Disc {track.disc:1d}"
    elif 10 <= album.disc_total <= 99:
        return f"Disc {track.disc:02d}"
    else:
        return f"Disc {track.disc:03d}"

def disc_and_track_num(track, album):
    if album.disc_total == 1:
        return f"{track.track_num:02d}"
    elif 2 <= album.disc_total <= 9:
        return f"{track.disc:1d}-{track.track_num:02d}"
    elif 10 <= album.disc_total <= 99:
        return f"{track.disc:02d}-{track.track_num:02d}"
    else:
        return f"{track.disc:03d}-{track.track_num:02d}"
