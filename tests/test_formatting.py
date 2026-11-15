"""Display formatting is deterministic and does not round calculation inputs."""
import unittest

from minecraft_playden.formatting import format_drive_space, format_size


class FormattingTests(unittest.TestCase):
    def test_unknown_zero_small_and_unit_boundaries(self):
        for value, expected in [(None, 'Unknown'), (-1, 'Unknown'), (-999, 'Unknown'),
                                (0, '0 B'), (1, '1 B'), (999, '999 B'),
                                (1000, '1.00 KB'), (1536, '1.54 KB'),
                                (1_000_000, '1.00 MB'), (123456789, '123.46 MB'),
                                (1_000_000_000, '1.00 GB'), (2**64 - 1, '18446744073.71 GB')]:
            with self.subTest(value=value):
                self.assertEqual(format_size(value), expected)

    def test_capacity_prefers_gb_without_zeroing_tiny_values(self):
        for value, expected in [(0, '0 B'), (1, '1 B'), (1000, '1.00 KB'),
                                (1_000_000, '1.00 MB'), (10_000_000, '0.01 GB'),
                                (123456789, '0.12 GB'), (1_000_000_000_000, '1000.00 GB')]:
            with self.subTest(value=value):
                self.assertEqual(format_size(value, prefer_gb=True), expected)

    def test_available_and_total_independently_unknown(self):
        self.assertEqual(format_drive_space(123456789, 500000000000), '0.12 GB free / 500.00 GB total')
        self.assertEqual(format_drive_space(None, -1), 'Unknown free / Unknown total')
        self.assertEqual(format_drive_space(0, None), '0 B free / Unknown total')
        self.assertEqual(format_drive_space(-1, 1000000000), 'Unknown free / 1.00 GB total')
