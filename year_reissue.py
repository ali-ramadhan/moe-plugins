import moe

@moe.hookimpl
def create_path_template_func():
    return [year_reissue]

def year_reissue(album):
    if album.year == album.original_year:
        return f"{album.year}"
    else:
        return f"{album.original_year}, RE-{album.year}"
