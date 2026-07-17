"""/admin/lab 回填的 zip 解析纯函数测试。"""
import io
import unittest
import zipfile

from admin.lab.backfill import MAX_PHOTOS, extract_photos


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class ExtractPhotosTest(unittest.TestCase):
    def test_filters_junk_and_sorts_by_basename(self) -> None:
        zip_bytes = _make_zip({
            "b.jpg": b"b-data",
            "a.PNG": b"a-data",
            "__MACOSX/c.jpg": b"junk",
            ".DS_Store": b"junk",
            "sub/.hidden.png": b"junk",
            "notes.txt": b"junk",
            "sub/d.jpeg": b"d-data",
        })

        photos = extract_photos(zip_bytes)

        self.assertEqual(
            [name for name, _ in photos],
            ["a.PNG", "b.jpg", "d.jpeg"],
        )
        self.assertEqual(photos[0][1], b"a-data")
        self.assertEqual(photos[2][1], b"d-data")

    def test_rejects_zip_without_photos(self) -> None:
        zip_bytes = _make_zip({"readme.txt": b"x"})
        with self.assertRaises(ValueError):
            extract_photos(zip_bytes)

    def test_rejects_invalid_zip_bytes(self) -> None:
        with self.assertRaises(ValueError):
            extract_photos(b"not a zip")

    def test_rejects_too_many_photos(self) -> None:
        zip_bytes = _make_zip({f"p{i:03d}.jpg": b"x" for i in range(MAX_PHOTOS + 1)})
        with self.assertRaises(ValueError):
            extract_photos(zip_bytes)

    def test_rejects_oversized_single_photo(self) -> None:
        zip_bytes = _make_zip({"big.jpg": b"\x00" * (15 * 1024 * 1024 + 1)})
        with self.assertRaises(ValueError):
            extract_photos(zip_bytes)
