"""
Tests for shuo/ivr_detector.py — IVR auto-detection.
"""

import pytest
from shuo.ivr_detector import IVRDetector, classify_number


# ---------------------------------------------------------------------------
# classify_number
# ---------------------------------------------------------------------------

class TestClassifyNumber:
    def test_au_13_number(self):
        assert classify_number("131171") is True
        assert classify_number("131000") is True

    def test_au_13_with_country_code(self):
        assert classify_number("+61131171") is True
        assert classify_number("61131171") is True

    def test_au_1300(self):
        assert classify_number("1300135090") is True
        assert classify_number("+611300135090") is True

    def test_au_1800(self):
        assert classify_number("1800800110") is True

    def test_us_tollfree(self):
        assert classify_number("+18005551234") is True
        assert classify_number("8005551234") is True
        assert classify_number("+18885551234") is True

    def test_uk_0800(self):
        assert classify_number("08001234567") is True
        assert classify_number("+4408001234567") is True

    def test_regular_au_mobile(self):
        assert classify_number("+61412345678") is False

    def test_regular_au_landline(self):
        assert classify_number("+61298765432") is False

    def test_us_regular(self):
        assert classify_number("+16175551234") is False

    def test_empty(self):
        assert classify_number("") is False

    def test_spaces_stripped(self):
        assert classify_number("13 11 71") is True
        assert classify_number("1300 135 090") is True


# ---------------------------------------------------------------------------
# IVRDetector — number-based detection
# ---------------------------------------------------------------------------

class TestIVRDetectorNumber:
    def test_service_number_detected_immediately(self):
        d = IVRDetector()
        d.setup(phone="131171")
        assert d.is_ivr is True

    def test_regular_number_not_detected(self):
        d = IVRDetector()
        d.setup(phone="+61412345678")
        assert d.is_ivr is False

    def test_force_flag_overrides_number(self):
        d = IVRDetector()
        d.setup(phone="+61412345678", force=True)
        assert d.is_ivr is True

    def test_on_detected_fires_on_number_match(self):
        fired = []
        d = IVRDetector()
        d.setup(phone="131171", on_detected=lambda: fired.append(1))
        assert fired == [1]

    def test_on_detected_fires_on_force(self):
        fired = []
        d = IVRDetector()
        d.setup(phone="+61412345678", force=True, on_detected=lambda: fired.append(1))
        assert fired == [1]

    def test_setup_idempotent_once_detected(self):
        fired = []
        d = IVRDetector()
        d.setup(phone="131171", on_detected=lambda: fired.append(1))
        d.setup(phone="131171", on_detected=lambda: fired.append(2))
        assert len(fired) == 1   # second setup is a no-op

    def test_analyze_returns_false_when_already_detected(self):
        d = IVRDetector()
        d.setup(phone="131171")
        result = d.analyze("Welcome to VicRoads. Press 1 for registration.")
        assert result is False   # already detected; no new transition


# ---------------------------------------------------------------------------
# IVRDetector — transcript-based detection
# ---------------------------------------------------------------------------

class TestIVRDetectorTranscript:
    def test_strong_phrase_triggers_detection(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        newly = d.analyze("Welcome to Acme Corp. Press 1 for sales, press 2 for support.")
        assert newly is True
        assert d.is_ivr is True

    def test_weak_phrases_accumulate(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        d.analyze("Please hold while we connect you.")           # +0.20 → 0.20
        d.analyze("We are currently experiencing high call volumes.")  # +0.40 → 0.60 ← threshold
        # By now the detector should have flipped
        assert d.is_ivr is True

    def test_human_speech_does_not_trigger(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        d.analyze("Hello, this is John speaking, how can I help you today?")
        d.analyze("Sure, let me look into that for you.")
        assert d.is_ivr is False

    def test_on_detected_fires_on_transcript(self):
        fired = []
        d = IVRDetector()
        d.setup(phone="+16175551234", on_detected=lambda: fired.append(1))
        assert fired == []
        d.analyze("Welcome to Acme Corp. Press 1 for sales.")
        assert fired == [1]

    def test_second_analyze_returns_false(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        d.analyze("Welcome to Acme Corp. Press 1 for sales.")
        newly = d.analyze("For billing, press 2.")
        assert newly is False   # already detected

    def test_thank_you_for_calling(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        newly = d.analyze("Thank you for calling our service centre.")
        assert newly is True

    def test_for_x_press_y_pattern(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        newly = d.analyze("For registration enquiries, press 1.")
        assert newly is True

    def test_partial_ivr_text_below_threshold(self):
        d = IVRDetector()
        d.setup(phone="+16175551234")
        newly = d.analyze("Please hold.")   # only +0.20
        assert newly is False
        assert d.is_ivr is False
