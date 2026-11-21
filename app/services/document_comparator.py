import difflib


def compare_texts(old_text: str, new_text: str):
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile="Старая версия",
        tofile="Новая версия",
        lineterm=""
    )

    return list(diff)
