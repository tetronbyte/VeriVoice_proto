"""Tests for the IVR welcome flow: language selection, menu, and action routing.

Covers:
  POST /twilio/voice/welcome
  POST /twilio/voice/welcome/language
  POST /twilio/voice/welcome/action
"""

import pytest

from conftest import parse_twiml


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/welcome
# ═══════════════════════════════════════════════════════════════════════════════


class TestWelcome:
    """Tests for the initial welcome prompt (language selection via DTMF)."""

    def test_returns_valid_twiml_with_gather(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome")
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]

        root = parse_twiml(resp.text)
        gathers = root.findall("Gather")
        assert len(gathers) == 1, "Expected exactly one <Gather> in welcome TwiML"

    def test_gather_action_points_to_language_endpoint(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome")
        root = parse_twiml(resp.text)

        gather = root.find("Gather")
        assert gather is not None
        assert gather.attrib["action"] == "/twilio/voice/welcome/language"
        assert gather.attrib.get("numDigits") == "1"

    def test_contains_say_for_english(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome")
        root = parse_twiml(resp.text)

        gather = root.find("Gather")
        say_elements = gather.findall("Say")
        assert len(say_elements) >= 1, "Expected at least one <Say> inside <Gather>"

        # The English prompt should mention "English" and "Press 1"
        say_text = say_elements[0].text
        assert "English" in say_text
        assert "1" in say_text

    def test_contains_play_for_swahili_tts(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome")
        root = parse_twiml(resp.text)

        gather = root.find("Gather")
        play_elements = gather.findall("Play")
        assert len(play_elements) >= 1, "Expected at least one <Play> inside <Gather> for Swahili TTS"

        # The Play element should have a URL (from the mocked TTS service)
        play_url = play_elements[0].text
        assert play_url is not None and play_url.startswith("http")

    def test_no_input_fallback_redirects_to_english(self, ivr_client):
        """After the Gather times out, the response should fall through to a
        redirect that defaults to English (Digits=1)."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome")
        root = parse_twiml(resp.text)

        # After <Gather>, there should be a fallback <Say> about no input
        # and a <Redirect> to the language endpoint with Digits=1
        say_elements = root.findall("Say")
        fallback_texts = [s.text for s in say_elements if s.text]
        assert any("No input" in t for t in fallback_texts), \
            f"Expected a 'No input' fallback message, got: {fallback_texts}"

        redirect = root.find("Redirect")
        assert redirect is not None, "Expected a <Redirect> after Gather timeout"
        assert "Digits=1" in redirect.text


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/welcome/language
# ═══════════════════════════════════════════════════════════════════════════════


class TestLanguageSelection:
    """Tests for language selection (Digits=1 for EN, Digits=2 for SW)."""

    def test_digit_1_selects_english(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "1"})
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None

        # English selection: the Gather's action should contain lang=en
        action = gather.attrib["action"]
        assert "lang=en" in action

        # The prompt should be spoken in English via <Say>
        say_elements = gather.findall("Say")
        assert len(say_elements) >= 1
        say_text = say_elements[0].text
        assert "English" in say_text

    def test_digit_2_selects_swahili(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "2"})
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None

        # Swahili selection: the Gather's action should contain lang=sw
        action = gather.attrib["action"]
        assert "lang=sw" in action

        # Swahili uses <Play> (gTTS), not <Say>
        play_elements = gather.findall("Play")
        assert len(play_elements) >= 1, "Swahili prompt should use <Play> for gTTS audio"

    def test_gather_action_contains_correct_lang_param(self, ivr_client):
        """Verify the action URL correctly encodes the selected language."""
        client, mock_redis, mock_update_call = ivr_client

        # English
        resp_en = client.post("/twilio/voice/welcome/language", data={"Digits": "1"})
        root_en = parse_twiml(resp_en.text)
        action_en = root_en.find("Gather").attrib["action"]
        assert action_en == "/twilio/voice/welcome/action?lang=en"

        # Swahili
        resp_sw = client.post("/twilio/voice/welcome/language", data={"Digits": "2"})
        root_sw = parse_twiml(resp_sw.text)
        action_sw = root_sw.find("Gather").attrib["action"]
        assert action_sw == "/twilio/voice/welcome/action?lang=sw"

    def test_menu_offers_three_options(self, ivr_client):
        """The action menu should mention enroll (1), authenticate (2), verify (3)."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome/language", data={"Digits": "1"})
        root = parse_twiml(resp.text)

        gather = root.find("Gather")
        say_elements = gather.findall("Say")
        menu_text = " ".join(s.text for s in say_elements if s.text)

        assert "1" in menu_text, "Menu should mention pressing 1"
        assert "2" in menu_text, "Menu should mention pressing 2"
        assert "3" in menu_text, "Menu should mention pressing 3"
        assert "enroll" in menu_text.lower(), "Menu should mention enrollment"
        assert "authenticate" in menu_text.lower(), "Menu should mention authentication"
        assert "verify" in menu_text.lower(), "Menu should mention verification"

    def test_no_digits_defaults_to_english(self, ivr_client):
        """When no Digits form field is sent, default to English."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post("/twilio/voice/welcome/language", data={})
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        gather = root.find("Gather")
        assert gather is not None
        action = gather.attrib["action"]
        assert "lang=en" in action


# ═══════════════════════════════════════════════════════════════════════════════
# POST /twilio/voice/welcome/action
# ═══════════════════════════════════════════════════════════════════════════════


class TestActionRouting:
    """Tests for DTMF action routing: 1=enroll, 2=auth, 3=verify."""

    # ── English routing ─────────────────────────────────────────────────────

    def test_digit_1_redirects_to_enroll_en(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=en",
            data={"Digits": "1"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/enroll?lang=en&step=0"

    def test_digit_2_redirects_to_authenticate_en(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=en",
            data={"Digits": "2"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/authenticate?lang=en"

    def test_digit_3_redirects_to_verify_en(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=en",
            data={"Digits": "3"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/verify/start?lang=en"

    # ── Swahili routing ─────────────────────────────────────────────────────

    def test_digit_1_redirects_to_enroll_sw(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=sw",
            data={"Digits": "1"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/enroll?lang=sw&step=0"

    def test_digit_2_redirects_to_authenticate_sw(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=sw",
            data={"Digits": "2"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/authenticate?lang=sw"

    def test_digit_3_redirects_to_verify_sw(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=sw",
            data={"Digits": "3"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/verify/start?lang=sw"

    # ── Invalid digit ───────────────────────────────────────────────────────

    def test_invalid_digit_shows_error_and_redirects(self, ivr_client):
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=en",
            data={"Digits": "9"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)

        # Should contain an error message about invalid selection
        say_elements = root.findall("Say")
        texts = [s.text for s in say_elements if s.text]
        assert any("Invalid" in t or "invalid" in t.lower() for t in texts), \
            f"Expected 'Invalid selection' message, got: {texts}"

        # Should redirect back to welcome
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/welcome"

    def test_invalid_digit_swahili_shows_error_and_redirects(self, ivr_client):
        """Invalid digit in Swahili mode should still redirect to welcome."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=sw",
            data={"Digits": "7"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)

        # Swahili invalid selection uses <Play> (gTTS), so check for Play element
        # or Say element depending on how _say_or_play handles it
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/welcome"

    def test_default_digit_routes_to_enroll(self, ivr_client):
        """When no Digits form field is provided, the default (1) routes to enroll."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action?lang=en",
            data={},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == "/twilio/voice/enroll?lang=en&step=0"

    def test_default_lang_is_english(self, ivr_client):
        """When lang query param is omitted, it should default to English."""
        client, mock_redis, mock_update_call = ivr_client
        resp = client.post(
            "/twilio/voice/welcome/action",
            data={"Digits": "1"},
        )
        assert resp.status_code == 200

        root = parse_twiml(resp.text)
        redirect = root.find("Redirect")
        assert redirect is not None
        assert "lang=en" in redirect.text
