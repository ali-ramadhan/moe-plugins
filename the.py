import moe

@moe.hookimpl
def create_path_template_func():
    return [the]

articles = ["the", "a", "an"]

def the(name):
    name_split = name.split()
    first_word = name_split[0]
    if first_word.lower() in articles:
        name_no_article = " ".join(name_split[1:])
        return f"{name_no_article}, {first_word}"
    else:
        return name
