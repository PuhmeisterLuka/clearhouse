import unicodedata


def ascii_fold(s: str) -> str:
    """'Đorđe Černe' -> 'Dorde Cerne'. NFKD can't decompose Đ/đ, so those are mapped by hand."""
    s = s.replace("Đ", "D").replace("đ", "d")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
