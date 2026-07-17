"""Parse uploaded zip into product/scene_role preview structure."""
from __future__ import annotations

import io
import zipfile

_VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


def _decode_zip_filename(info: zipfile.ZipInfo) -> str:
    """zipfile defaults to CP437; re-decode if the UTF-8 flag bit isn't set.

    Tries UTF-8 first (Mac archives often lack the flag bit but ARE utf-8),
    then GBK (Windows default). Returns the original name if both fail.
    """
    if info.flag_bits & 0x800:
        # UTF-8 flag set — zipfile already decoded it correctly
        return info.filename
    # zipfile decoded raw bytes as CP437; re-encode to recover raw bytes
    raw = info.filename.encode('cp437', errors='replace')
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _is_video(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _VIDEO_EXT)


def _is_mac_metadata(name: str) -> bool:
    """Detect Mac AppleDouble / Finder metadata files that should not appear in the preview."""
    # __MACOSX/ directory tree (parallel resource fork directory)
    if name.startswith("__MACOSX/") or "/__MACOSX/" in name:
        return True
    # AppleDouble files: ._<original_filename> alongside the real file
    basename = name.rsplit("/", 1)[-1]
    if basename.startswith("._"):
        return True
    # Finder metadata
    if basename == ".DS_Store":
        return True
    return False


def parse_zip_structure(
    zip_bytes: bytes,
    *,
    existing_tree: dict[str, set[str]],
) -> list[dict]:
    """解析 zip 内部目录结构，按 product/scene_role 分组返回预览。

    Args:
        zip_bytes: zip 文件原始字节
        existing_tree: 当前租户已有的 {product: {scene_role, ...}}

    Returns:
        List of {product, scene_role, files, status} 预览项。
        status: "existing" | "new" | "ignored"
    """
    grouped: dict[tuple[str | None, str | None], list[str]] = {}
    ignored: list[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            name = _decode_zip_filename(info)
            if name.endswith("/"):
                continue  # skip directory entries
            if _is_mac_metadata(name):
                continue  # silently skip Mac metadata — don't even add to "ignored"
            parts = name.split("/")
            if not _is_video(name) or len(parts) > 3 or len(parts) < 2:
                ignored.append(name)
                continue
            if len(parts) == 2:
                product, filename = parts
                scene_role = None
            else:
                product, scene_role, filename = parts
            grouped.setdefault((product, scene_role), []).append(filename)

    preview: list[dict] = []
    for (product, scene_role), files in sorted(grouped.items()):
        if product in existing_tree:
            if scene_role is None or scene_role in existing_tree[product]:
                status = "existing"
            else:
                status = "new"
        else:
            status = "new"
        preview.append({
            "product": product,
            "scene_role": scene_role,
            "files": sorted(files),
            "status": status,
        })

    if ignored:
        preview.append({
            "product": None,
            "scene_role": None,
            "files": sorted(ignored),
            "status": "ignored",
        })

    return preview
