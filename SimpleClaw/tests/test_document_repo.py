"""Tests for tenant document repository mapping."""

from __future__ import annotations

import unittest

from Mojing.storage.document_repo import _doc_type


class DocumentRepoMappingTest(unittest.TestCase):
    def test_known_doc_types(self) -> None:
        self.assertEqual(_doc_type("USER.md"), "user")
        self.assertEqual(_doc_type("SOUL.md"), "soul")
        self.assertEqual(_doc_type("SKIN_DIARY_TODO.md"), "skin_diary_todo")

    def test_unknown_doc_type_falls_back_to_doc(self) -> None:
        self.assertEqual(_doc_type("OTHER.md"), "doc")


if __name__ == "__main__":
    unittest.main()
