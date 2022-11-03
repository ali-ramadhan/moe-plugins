import moe

@moe.hookimpl
def create_path_template_func():
    return [disc_and_track]

def format_disc_dir(track, album):
    if album.disc_total == 1:
        return ""
    elif 2 <= album.disc_total <= 9:
        return f"Disc {track.disc:1d}/"
    elif 10 <= album.disc_total <= 99:
        return f"Disc {track.disc:02d}/"
    elif 101 <= album.disc_total <= 999:
        return f"Disc {track.disc:03d}/"

def disc_and_track(track, album):
    disc_dir = format_disc_dir(track, album)
    return f"{disc_dir}{track.track_num:02d}"
