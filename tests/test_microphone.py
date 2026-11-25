import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from integration.microphone import clipping_ratio


class ClippingRatioTests(unittest.TestCase):
    def make_wav(self, samples):
        temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp.close()
        path = Path(temp.name)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(array("h", samples).tobytes())
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_clean_audio_has_zero_clipping(self):
        path = self.make_wav([0, 100, -100, 1000, -1000, 20000, -20000])
        self.assertEqual(clipping_ratio(path), 0.0)

    def test_counts_positive_and_negative_near_full_scale(self):
        path = self.make_wav([0, 32760, -32768, 100, -100])
        self.assertAlmostEqual(clipping_ratio(path), 0.4)


if __name__ == "__main__":
    unittest.main()
