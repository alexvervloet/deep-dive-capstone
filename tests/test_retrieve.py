"""
Retrieval must query with the model the index was built with.

The index records both its stack and its embed model. A stack's default model
can change (claude moved from voyage-3.5 to voyage-4 on 2026-10-06), and an
index built before that only makes sense next to query vectors from the old
model. Offline: embed() is replaced, so no key and no network.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo import retrieve  # noqa: E402


def _index(embed_model):
    chunk = {"text": "alpha beta", "vector": [1.0, 0.0], "path": "a.md", "start": 1, "end": 1}
    index = {"stack": "claude", "chunks": [chunk]}
    if embed_model is not None:
        index["embed_model"] = embed_model
    return index


class TestQueryModel(unittest.TestCase):
    def _model_used(self, index):
        seen = {}

        def fake_embed(texts, stack, input_type="document", model=None):
            seen.update(stack=stack, model=model, input_type=input_type)
            return [[1.0, 0.0]], 1

        with mock.patch.object(retrieve, "embed", fake_embed):
            retrieve.retrieve("alpha", index, k=1)
        return seen

    def test_uses_the_model_recorded_in_the_index(self):
        seen = self._model_used(_index("voyage-3.5"))
        self.assertEqual(seen["model"], "voyage-3.5")
        self.assertEqual(seen["stack"], "claude")
        self.assertEqual(seen["input_type"], "query")

    def test_old_index_without_a_model_falls_back_to_the_stack_default(self):
        self.assertIsNone(self._model_used(_index(None))["model"])


if __name__ == "__main__":
    unittest.main()
