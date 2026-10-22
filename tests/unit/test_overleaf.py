from jobagent.tools.overleaf import strip_overleaf_banner

BANNER = (
    "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
    "%\n"
    "% Welcome to Overleaf --- just edit your LaTeX on the left,\n"
    "% and we'll compile it for you on the right. If you open the\n"
    "% 'Share' menu, you can invite other users to edit at the same\n"
    "% time. See www.overleaf.com/learn for more info. Enjoy!\n"
    "%\n"
    "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
)
SOURCE = "%-------------------------\n% Resume in Latex\n\\documentclass{article}\n\\begin{document}x\\end{document}\n"


def test_banner_is_removed():
    assert strip_overleaf_banner(BANNER + SOURCE) == SOURCE
    assert strip_overleaf_banner(BANNER + "\n" + SOURCE) == SOURCE


def test_text_without_banner_is_unchanged():
    assert strip_overleaf_banner(SOURCE) == SOURCE


def test_user_comment_blocks_are_kept():
    own = "%%%%%%%%%%%%%%%%%%%%%%%%\n% My own header\n%%%%%%%%%%%%%%%%%%%%%%%%\n" + SOURCE
    assert strip_overleaf_banner(own) == own


def test_crlf_is_normalised():
    assert strip_overleaf_banner((BANNER + SOURCE).replace("\n", "\r\n")) == SOURCE
