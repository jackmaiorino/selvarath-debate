"""Small platform primitives for crash-durable namespace publication."""
from __future__ import annotations

import os
from pathlib import Path


def _windows_extended_path(path: Path) -> str:
    """Return one lexical absolute Win32 path without dereferencing its final entry."""
    if os.name != "nt":
        raise OSError("Windows path normalization requested on a non-Windows platform")
    absolute = os.path.abspath(os.fspath(path))
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return f"\\\\?\\UNC\\{absolute[2:]}"
    return f"\\\\?\\{absolute}"


def _windows_is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & 0x00000400)


def windows_move_write_through(
    source: Path,
    destination: Path,
    *,
    replace_existing: bool,
) -> None:
    """Move one same-directory stage and wait for documented disk persistence."""
    if os.name != "nt":
        raise OSError("Windows write-through move requested on a non-Windows platform")
    source = Path(source)
    destination = Path(destination)
    source_parent = os.path.normcase(os.path.abspath(os.fspath(source.parent)))
    destination_parent = os.path.normcase(
        os.path.abspath(os.fspath(destination.parent)))
    if source_parent != destination_parent:
        raise OSError("write-through publication stage must share the destination directory")
    if not replace_existing and os.path.lexists(destination):
        raise FileExistsError(
            f"write-through publication destination exists: {destination}")
    if replace_existing and _windows_is_reparse_point(destination):
        raise OSError(
            f"refusing to replace a Windows reparse-point destination: {destination}")

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move_file_ex.restype = wintypes.BOOL
    movefile_replace_existing = 0x00000001
    movefile_write_through = 0x00000008
    flags = movefile_write_through
    if replace_existing:
        flags |= movefile_replace_existing
    if move_file_ex(
        _windows_extended_path(source),
        _windows_extended_path(destination),
        flags,
    ):
        return
    error = ctypes.get_last_error()
    error_file_exists = 80
    error_already_exists = 183
    if not replace_existing and (
        error in (error_file_exists, error_already_exists)
        or destination.exists()
        or destination.is_symlink()
    ):
        raise FileExistsError(
            error, f"write-through publication destination exists: {destination}")
    raise OSError(
        error,
        f"could not write-through move {source} to {destination}",
    )
