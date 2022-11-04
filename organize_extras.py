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
