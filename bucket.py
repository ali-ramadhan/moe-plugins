import moe

@moe.hookimpl
def create_path_template_func():
    return [bucket]

def bucket(artist):
    first_char = artist[0]
    if first_char.isalpha():
        return first_char.upper()
    elif first_char.isnumeric():
        return "0-9"
    else:
        return "#-!"
