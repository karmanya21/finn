"""Extraction quality on the hand-labelled gold set must not regress.

Uses cached model answers, so it's free to re-run. Skipped on a fresh clone (no
downloaded transcripts) or without a Gemini key, where it would need live calls.
"""

import os
import re
import subprocess
import sys
import unittest

from finn.config import GEMINI_API_KEY, ROOT, TEXT

MIN_RECALL = 19  # of 20 gold promises


class ExtractionQuality(unittest.TestCase):
    @unittest.skipUnless((TEXT / "transcripts").exists() and GEMINI_API_KEY, "needs transcripts and a Gemini key")
    def test_gold_recall(self):
        out = subprocess.run([sys.executable, "-m", "scripts.eval_extraction"], cwd=ROOT / "backend",
                             capture_output=True, text=True, timeout=900, env={**os.environ})
        m = re.search(r"Extraction recall: (\d+)/(\d+)", out.stdout)
        self.assertIsNotNone(m, out.stdout[-2000:] + out.stderr[-2000:])
        self.assertGreaterEqual(int(m.group(1)), MIN_RECALL, out.stdout[-2000:])
        self.assertIn("Pre-filter recall: 20/20", out.stdout)


if __name__ == "__main__":
    unittest.main()
