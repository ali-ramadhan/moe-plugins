import moe

@moe.hookimpl
def create_path_template_func():
    return [album_bit_depth]

def album_bit_depth(album):
    bit_depths = [track.bit_depth for track in album.tracks]
    bit_depths = list(set(bit_depths))

    if len(bit_depths) == 1:
        return f"{bit_depths[0]}bit"
    else:
        return "+".join(map(str, bit_depths)) + "bit"
