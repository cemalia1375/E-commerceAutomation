from __future__ import annotations

import unittest

from Mojing.storage.image_repo import _profile_job_lookup_candidates


class ImageRepoProfileLookupTest(unittest.TestCase):
    def test_profile_message_id_can_be_image_job_id(self) -> None:
        candidates = _profile_job_lookup_candidates(
            {
                "message_id": "job-123",
                "image_url": "https://example.com/face.png",
                "analysis_id": "skin_job-123",
            }
        )

        self.assertEqual(
            candidates,
            [
                ("message_id = %s", "job-123"),
                ("job_id = %s", "job-123"),
                ("image_ref = %s", "https://example.com/face.png"),
                ("image_id = %s", "skin_job-123"),
            ],
        )

    def test_profile_analysis_id_can_reference_image_job_id(self) -> None:
        candidates = _profile_job_lookup_candidates({"analysis_id": "skin_job-456"})

        self.assertEqual(
            candidates,
            [
                ("image_id = %s", "skin_job-456"),
                ("job_id = %s", "job-456"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
