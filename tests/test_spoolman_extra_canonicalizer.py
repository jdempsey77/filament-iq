"""Tests for scripts/spoolman_extra_canonicalizer.py — P1 canonicalizer foundation."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from spoolman_extra_canonicalizer import (
    canonicalize_rfid_tag_uid,
    canonicalize_ha_spool_uuid,
    canonicalize_extra_scalar,
    encode_extra_json_string,
    is_double_encoded,
    validate_extra_value_no_quotes,
)


class TestCanonicalizeRfidTagUid(unittest.TestCase):

    def test_plain_hex_input(self):
        self.assertEqual(canonicalize_rfid_tag_uid("0A1B2C3D4E5F6A7B"), "0A1B2C3D4E5F6A7B")

    def test_json_encoded_input(self):
        self.assertEqual(canonicalize_rfid_tag_uid('"0A1B2C3D4E5F6A7B"'), "0A1B2C3D4E5F6A7B")

    def test_all_zero_sentinel(self):
        self.assertEqual(canonicalize_rfid_tag_uid("0000000000000000"), "")

    def test_empty_string(self):
        self.assertEqual(canonicalize_rfid_tag_uid(""), "")

    def test_none_input(self):
        self.assertEqual(canonicalize_rfid_tag_uid(None), "")

    def test_double_encoded_input(self):
        double = '"\\"0A1B2C3D4E5F6A7B\\""'
        result = canonicalize_rfid_tag_uid(double)
        self.assertIn(result, ("0A1B2C3D4E5F6A7B", ""))

    def test_mixed_case_input(self):
        self.assertEqual(canonicalize_rfid_tag_uid("0a1b2c3d4e5f6a7b"), "0A1B2C3D4E5F6A7B")

    def test_whitespace_input(self):
        self.assertEqual(canonicalize_rfid_tag_uid("  0A1B2C3D4E5F6A7B  "), "0A1B2C3D4E5F6A7B")

    def test_bare_quotes_literal(self):
        self.assertEqual(canonicalize_rfid_tag_uid('""'), "")

    def test_invalid_hex_returns_empty(self):
        self.assertEqual(canonicalize_rfid_tag_uid("ZZZZZZZZZZZZZZZZ"), "")

    def test_short_hex_returns_empty(self):
        self.assertEqual(canonicalize_rfid_tag_uid("0A1B"), "")

    def test_json_encoded_all_zero(self):
        self.assertEqual(canonicalize_rfid_tag_uid('"0000000000000000"'), "")


class TestCanonicalizeHaSpoolUuid(unittest.TestCase):

    def test_valid_uuid(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        self.assertEqual(canonicalize_ha_spool_uuid(uuid), uuid)

    def test_json_encoded_uuid(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        self.assertEqual(canonicalize_ha_spool_uuid(f'"{uuid}"'), uuid)

    def test_invalid_uuid(self):
        self.assertEqual(canonicalize_ha_spool_uuid("not-a-uuid"), "")

    def test_empty(self):
        self.assertEqual(canonicalize_ha_spool_uuid(""), "")

    def test_none(self):
        self.assertEqual(canonicalize_ha_spool_uuid(None), "")

    def test_uuid_with_quotes_and_backslash(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        mangled = f'\\"{uuid}\\"'
        result = canonicalize_ha_spool_uuid(mangled)
        self.assertEqual(result, uuid)


class TestCanonicalizeExtraScalar(unittest.TestCase):

    def test_plain_string(self):
        self.assertEqual(canonicalize_extra_scalar("hello"), "hello")

    def test_json_encoded(self):
        self.assertEqual(canonicalize_extra_scalar('"hello"'), "hello")

    def test_none(self):
        self.assertEqual(canonicalize_extra_scalar(None), "")

    def test_whitespace(self):
        self.assertEqual(canonicalize_extra_scalar("  padded  "), "padded")


class TestEncodeExtraJsonString(unittest.TestCase):

    def test_plain_string(self):
        result = encode_extra_json_string("ABC123")
        self.assertEqual(result, '"ABC123"')

    def test_already_encoded_raises(self):
        with self.assertRaises(ValueError):
            encode_extra_json_string('"ABC123"')

    def test_empty_string(self):
        self.assertEqual(encode_extra_json_string(""), '""')

    def test_none_encodes_as_empty(self):
        self.assertEqual(encode_extra_json_string(None), '""')

    def test_string_with_special_chars(self):
        result = encode_extra_json_string("hello\nworld")
        self.assertEqual(result, '"hello\\nworld"')


class TestIsDoubleEncoded(unittest.TestCase):

    def test_double_encoded_true(self):
        double = '"\\"ABC\\""'
        self.assertTrue(is_double_encoded(double))

    def test_single_encoded_false(self):
        self.assertFalse(is_double_encoded('"ABC"'))

    def test_plain_false(self):
        self.assertFalse(is_double_encoded("ABC"))

    def test_none_false(self):
        self.assertFalse(is_double_encoded(None))

    def test_empty_false(self):
        self.assertFalse(is_double_encoded(""))

    def test_nested_json_true(self):
        import json
        double = json.dumps(json.dumps("test"))
        self.assertTrue(is_double_encoded(double))


class TestValidateExtraValueNoQuotes(unittest.TestCase):

    def test_clean_input_passes(self):
        result = validate_extra_value_no_quotes("clean_value")
        self.assertEqual(result, "clean_value")

    def test_json_encoded_clean_passes(self):
        result = validate_extra_value_no_quotes('"clean"')
        self.assertEqual(result, "clean")

    def test_raw_quote_raises(self):
        with self.assertRaises(ValueError):
            validate_extra_value_no_quotes('has"quote')

    def test_backslash_raises(self):
        with self.assertRaises(ValueError):
            validate_extra_value_no_quotes("has\\backslash")

    def test_none_passes(self):
        result = validate_extra_value_no_quotes(None)
        self.assertEqual(result, "")

    def test_empty_passes(self):
        result = validate_extra_value_no_quotes("")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
