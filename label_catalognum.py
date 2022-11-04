import moe

@moe.hookimpl
def create_path_template_func():
    return [label_catalognum]

def label_catalognum(album):
    if album.label and album.catalog_num:
        return f" {{{album.label} {album.catalog_num}}}"
    elif album.label:
        return f" {{{album.label}}}"
    elif album.catalog_num:
        return f" {{{album.catalog_num}}}"
    else:
        return ""
