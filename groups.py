"""Dashboard sections: which group each link's tile is shown under."""

OTHER_GROUP = "Other"      # the section for links with no group set
MAX_GROUP_LEN = 60


def load_order(config):
    """[dashboard] group_order: sections top to bottom, comma-separated.

    Groups not listed still show, after these, alphabetically.
    """
    raw = config.get("dashboard", "group_order", fallback="")
    return [clean_group(g) for g in raw.split(",") if clean_group(g)]


def clean_group(text):
    """'  Mer   Team ' -> 'Mer Team'. Stray spaces must not split a group."""
    return " ".join((text or "").split())[:MAX_GROUP_LEN]


def suggestions(order, in_use):
    """Names for the Group field: the configured ones, then any others."""
    seen = {name.casefold() for name in order}
    return list(order) + [n for n in in_use if n.casefold() not in seen]


def canonical(text, order, in_use):
    """Clean `text`, and spell it like a known group if it is one.

    'mer  TEAM' is saved as 'Mer Team', so the Manage table reads the same
    as the dashboard heading.
    """
    name = clean_group(text)
    for k in suggestions(order, in_use):
        if k.casefold() == name.casefold():
            return k
    return name


def is_down(r):
    return bool(r["link"]["enabled"] and r["check"] and not r["check"]["ok"])


def is_up(r):
    return bool(r["link"]["enabled"] and r["check"] and r["check"]["ok"])


def group_rows(rows, order):
    """Split dashboard rows into sections, one per group.

    Returns [{"name", "rows", "down"}]: groups named in `order` first, in
    that order, then any others alphabetically, then OTHER_GROUP for links
    with no group. Names match ignoring case, so 'mer team' typed on one
    link still joins 'Mer Team'. Inside a group, down apps come first -
    they are why you looked.
    """
    rank = {name.casefold(): i for i, name in enumerate(order)}
    display = {name.casefold(): name for name in order}
    buckets = {}
    for r in rows:
        name = clean_group(r["link"]["group_name"]) or OTHER_GROUP
        key = name.casefold()
        display.setdefault(key, name)
        buckets.setdefault(key, []).append(r)

    def sort_key(key):
        if key in rank:
            return (0, rank[key], "")
        if key == OTHER_GROUP.casefold():
            return (2, 0, "")
        return (1, 0, key)

    return [{"name": display[key],
             "rows": sorted(buckets[key], key=lambda r: not is_down(r)),
             "down": sum(1 for r in buckets[key] if is_down(r)),
             "online": sum(1 for r in buckets[key] if is_up(r)),
             "active": sum(1 for r in buckets[key] if r["link"]["enabled"])}
            for key in sorted(buckets, key=sort_key)]


def find(sections, name):
    """The section called `name` (any case), or None - the landing page."""
    wanted = clean_group(name).casefold()
    return next((g for g in sections
                 if wanted and g["name"].casefold() == wanted), None)
