"""ExportPackageTool unit tests (unittest.IsolatedAsyncioTestCase 风格)."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

import pytest

from Flowcut.tools.export_package import ExportPackageTool


@pytest.mark.unit
class ExportPackageToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_submits_task(self) -> None:
        runtime = MagicMock()
        runtime.submit_task = AsyncMock(return_value="task-123")
        tool = ExportPackageTool(runtime=runtime)

        envelope = await tool.prepare_task(
            script_id=1, material_ids=[10, 11], tenant_key="t1",
        )

        self.assertEqual(envelope.task_type, "export_package")
        self.assertEqual(envelope.stream, "flowcut:export_package")
        self.assertEqual(envelope.payload["script_id"], 1)
        self.assertEqual(envelope.payload["material_ids"], [10, 11])

    async def test_export_rejects_empty_materials(self) -> None:
        tool = ExportPackageTool(runtime=MagicMock())

        with self.assertRaises(ValueError):
            await tool.prepare_task(
                script_id=1, material_ids=[], tenant_key="t1",
            )
