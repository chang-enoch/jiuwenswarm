"""Cheap filesystem snapshots; no polling thread or persistent index cache."""

from pathlib import Path
import os
import stat


def file_stamp(path: Path):
    """Include file identity to detect atomic replacements retaining mtime."""
    try:
        value = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return _stamp(value)


def _stamp(value):
    return (value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino)


def _is_link(value):
    # Junctions and other Windows reparse points also need containment checks.
    return (stat.S_ISLNK(value.st_mode)
            or bool(getattr(value, 'st_file_attributes', 0) & 0x400))


def catalog_signature(roots):
    """Scan only root SKILL.md entries, with the same containment policy as indexing."""
    entries = []
    for root in roots:
        root = Path(root).resolve()
        try:
            with os.scandir(root) as scan:
                directories = sorted(scan, key=lambda entry: entry.name)
        except (FileNotFoundError, NotADirectoryError):
            continue
        for directory in directories:
            if directory.name.startswith(('.', '_')):
                continue
            try:
                directory_stat = directory.stat(follow_symlinks=False)
                if _is_link(directory_stat):
                    parent = Path(directory.path).resolve()
                    if not parent.is_relative_to(root) or not parent.is_dir():
                        continue
                elif directory.is_dir(follow_symlinks=False):
                    parent = Path(directory.path)
                else:
                    continue
                path = parent / 'SKILL.md'
                value = path.lstat()
                # Ordinary directories/files are already under the resolved
                # root. Avoid opening every full path just to resolve it again.
                resolved = path.resolve() if _is_link(value) else path
                if not resolved.is_relative_to(root):
                    continue
                if _is_link(value):
                    value = resolved.stat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            if not stat.S_ISREG(value.st_mode):
                continue
            entries.append((str(Path(directory.path) / 'SKILL.md'), str(resolved), _stamp(value)))
            if len(entries) > 10000:
                raise RuntimeError('Local skill catalog exceeds the 10000 entry indexing limit')
    return tuple(entries)


def sources_unchanged(observed):
    return all(file_stamp(path) == value for path, value in observed.items())
