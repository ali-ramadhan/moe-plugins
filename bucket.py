import moe


@moe.hookimpl
def create_path_template_func():
    return [bucket]


def bucket(name):
    if not name:
        raise ValueError("Cannot bucket an empty name")
    first_char = name[0]
    if first_char.isalpha():
        return first_char.upper()
    elif first_char.isnumeric():
        return "0-9"
    else:
        return "#-!"
