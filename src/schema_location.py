"""Render a schema error location so the author can find it in their file.

jsonschema reports a path of keys and list ORDINALS: ``effects.17``. In a
thousand-line contract that is not a locator -- the ordinal appears nowhere in
the file, so it cannot be grepped, and finding it means counting list items by
hand. Every item in these lists carries a ``name``, which is the string the
author would actually search for, so an integer segment is rendered with the
name of the item it selects.

Only a name that is genuinely there is printed. An unnamed item stays a bare
ordinal rather than being given a label the file does not contain.
"""

NAME_KEYS = ("name", "id", "rule")

# A finding is one line, and a location is a fragment of it. The value spliced
# in here is written by the contract author and the schema accepts a newline
# inside it, so an effect named `deploy\n  at work.X` would put a second line
# into the terminal that reads as another finding's location -- the tool
# reporting something the contract said as though it were something the tool
# found. Same hole `_one_line` closes for the message half (dr-sbth5); it is
# reopened by any new place a contract value reaches the terminal.
_LABEL_MAX = 80


def _flatten(text):
    """Collapse a spliced value to a single line of printable characters."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > _LABEL_MAX:
        cleaned = cleaned[:_LABEL_MAX - 1] + "\u2026"
    return cleaned


def _item_label(item):
    """`key=value`, not `key: value`.

    The line this lands in is `  at <location>: <message>`, so a consumer
    splits it on the first `": "`. A colon inside the location moves that
    split and silently reassigns the path and the message.
    """
    if not isinstance(item, dict):
        return None
    for key in NAME_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            flat = _flatten(value)
            if flat:
                return f"{key}={flat}"
    return None


def describe(doc, path_parts, sep=".", root="(document root)"):
    """Render ``path_parts`` against ``doc``, naming each indexed item.

    ``doc`` is the document the error came from; it is walked in step with the
    path so an integer segment can be resolved to the item it selects. A walk
    that falls off the document degrades to the bare path rather than raising.
    """
    parts = list(path_parts)
    if not parts:
        return root
    rendered = []
    cursor = doc
    for part in parts:
        label = None
        if isinstance(part, int):
            if isinstance(cursor, list) and 0 <= part < len(cursor):
                label = _item_label(cursor[part])
                cursor = cursor[part]
            else:
                cursor = None
        else:
            cursor = cursor.get(part) if isinstance(cursor, dict) else None
        rendered.append(f"{part} ({label})" if label else str(part))
    return sep.join(rendered)
