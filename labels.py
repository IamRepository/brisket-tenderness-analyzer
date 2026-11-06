"""Text helpers shared by the app and the PDF report.

Kept apart from the page code so they can be tested without starting Streamlit.
"""
from __future__ import annotations

from collections import Counter
import re

# Characters that Streamlit's Markdown would act on in a label or message.
_MARKDOWN = re.compile(r"([\\`*_\[\]()<>#~$|!])")
_WWW = re.compile(r"www\.", re.IGNORECASE)
_ZERO_WIDTH_SPACE = "\u200b"


def md_safe(value) -> str:
    """Text from a file, made safe to show in a Streamlit label or message.

    Column headers and error texts come from the uploaded file. Left as they
    are, a header such as "[link](https://example.com)" would show a live link
    and "![x](https://example.com/p.png)" would load an outside image. Markdown
    characters are escaped, and a zero-width space is put after ":", before "@"
    and inside "www." so that web addresses, e-mail addresses and :emoji:
    codes are not turned into links or pictures either. The text reads the same.
    """
    text = _MARKDOWN.sub(r"\\\1", str(value))
    text = text.replace(":", ":" + _ZERO_WIDTH_SPACE).replace("@", _ZERO_WIDTH_SPACE + "@")
    return _WWW.sub(lambda m: m.group(0)[:3] + _ZERO_WIDTH_SPACE + ".", text)


def unique_label(role, file_label, column, existing) -> str:
    """'Role — column', or 'Role — File / column' when that name is already taken."""
    base = f"{role} — {column}"
    return base if base not in existing else f"{role} — {file_label} / {column}"


def environment_stream_names(streams) -> dict:
    """Readable, distinct names for environment streams.

    streams: {key: (role name, file label, column)}. A role with one stream is
    named by the role alone. When several streams share a role, each is named
    by its column as well (and by its file when the column name repeats), so
    two hold sensors are never drawn as one line.
    """
    per_role = Counter(role for role, _, _ in streams.values())
    per_column = Counter((role, column) for role, _, column in streams.values())
    names = {}
    for key, (role, file_label, column) in streams.items():
        if per_role[role] == 1:
            names[key] = role
        elif per_column[(role, column)] == 1 or not file_label:
            names[key] = f"{role} ({column})"
        else:
            names[key] = f"{role} ({file_label} / {column})"
    return names


def plotly_text(value) -> str:
    """Text from a file, made safe to give to Plotly as a series name.

    Plotly reads a small set of HTML tags (such as <a href>) in legends and
    hover text, so <, > and & are written as entities and shown as typed.
    """
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
