import moe

@moe.hookimpl
def create_path_template_func():
    return [organize_extras]

def organize_extras(extra):
    album = extra.album_obj
    print(f"Extra path: {extra.path}, name: {extra.path.name}")

    if extra.path.name == "cover.jpg":
        return f"{album.title}.jpg"
    else:
        return extra.path.name

def new_path_maybe_disc_specific(path, ext="log"):
    match_multidisc = re.search(f".*(?:disc|cd)\s*(\d+).*\.{ext}$", path, re.IGNORECASE)
    if match_multidisc:
        disc_num = int(match_multidisc.group(1))
        return f"Disc {disc_num:1d}/Album name - Disc {disc_num:1d}.{ext}"

    match_1disc = re.search(f".*\.{ext}$", path)
    if match_1disc:
        return f"Album name.{ext}"

for path in paths:
    new_path = new_path_maybe_disc_specific(path)
    print(f"{path} -> {new_path}")

# .cue, .log -> "Disc N/Album name - Disc N.ext"
# (cover|folder).(jpg|jpeg|png) -> "Album name.ext"
# (scan|scans|artwork) -> move entire folder and preserve filenames
# Extra jpg/png/pdf/txt/etc. -> move to base album dir and preserve filename
    
