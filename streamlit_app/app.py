"""VeriVoice Streamlit Demo UI — calls the FastAPI backend for all four flows."""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API = f"{BACKEND_URL}/api/v1"

st.set_page_config(page_title="VeriVoice Demo", layout="wide")
st.title("VeriVoice — Voice Authentication Demo")

page = st.sidebar.radio("Navigate", ["Enroll", "Authenticate", "Consent", "Service Access"])


# ═════════════════════════════════════════════════════════════════════════════
# ENROLL
# ═════════════════════════════════════════════════════════════════════════════
if page == "Enroll":
    st.header("Voice Enrollment")
    st.markdown("Register a citizen with **5 voice samples**.")

    with st.form("enroll_form"):
        national_id = st.text_input("National ID Number", placeholder="KE-123456")
        language = st.selectbox("Preferred Language", ["en", "sw"], index=0)
        phone = st.text_input("Phone Number (E.164)", placeholder="+254700000000")

        st.markdown("---")
        st.subheader("Upload 5 Audio Samples")
        audio_files = []
        cols = st.columns(5)
        for i in range(5):
            with cols[i]:
                f = st.file_uploader(f"Sample {i + 1}", type=["wav", "mp3", "ogg"], key=f"enroll_audio_{i}")
                audio_files.append(f)

        submitted = st.form_submit_button("Enroll Citizen")

    if submitted:
        if not national_id or not phone:
            st.error("National ID and Phone Number are required.")
        elif any(f is None for f in audio_files):
            st.error("All 5 audio samples are required.")
        else:
            files = [
                ("audio_files", (f"sample_{i}.wav", audio_files[i].getvalue(), "audio/wav"))
                for i in range(5)
            ]
            data = {
                "national_id_number": national_id,
                "preferred_language": language,
                "phone_number": phone,
            }
            with st.spinner("Enrolling..."):
                try:
                    resp = httpx.post(f"{API}/enroll", data=data, files=files, timeout=120.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        st.success(f"Enrolled successfully!")
                        st.json(body)
                    else:
                        st.error(f"Error {resp.status_code}: {resp.json().get('detail', resp.text)}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}. Is it running?")


# ═════════════════════════════════════════════════════════════════════════════
# AUTHENTICATE
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Authenticate":
    st.header("Voice Authentication")
    st.markdown("Dual-stage: **voice biometric** + **phrase transcript** match.")

    citizen_id = st.text_input("Citizen ID (UUID)", key="auth_citizen_id")
    lang = st.selectbox("Language", ["en", "sw"], index=0, key="auth_lang")

    # ── Get Challenge ────────────────────────────────────────────────────
    if st.button("Get Challenge Phrase"):
        try:
            resp = httpx.get(f"{API}/challenge", params={"language": lang}, timeout=30.0)
            if resp.status_code == 200:
                challenge = resp.json()
                st.session_state["challenge"] = challenge
                st.success(f"Challenge: **{challenge['phrase_text']}**")
                if challenge.get("audio_url"):
                    try:
                        st.audio(challenge["audio_url"])
                    except Exception:
                        st.info("Audio file path returned — playback may require local file access.")
            else:
                st.error(f"Error {resp.status_code}: {resp.text}")
        except httpx.ConnectError:
            st.error(f"Cannot connect to backend at {BACKEND_URL}.")

    # Show current challenge if stored
    if "challenge" in st.session_state:
        st.info(f"Active challenge: {st.session_state['challenge']['phrase_text']}")

    # ── Authenticate ─────────────────────────────────────────────────────
    auth_audio = st.file_uploader("Upload spoken response", type=["wav", "mp3", "ogg"], key="auth_audio")

    if st.button("Authenticate"):
        if not citizen_id:
            st.error("Enter a Citizen ID.")
        elif "challenge" not in st.session_state:
            st.error("Get a challenge phrase first.")
        elif auth_audio is None:
            st.error("Upload your spoken response audio.")
        else:
            data = {
                "citizen_id": citizen_id,
                "challenge_phrase_id": st.session_state["challenge"]["challenge_id"],
            }
            files = [("audio_file", ("response.wav", auth_audio.getvalue(), "audio/wav"))]
            with st.spinner("Authenticating..."):
                try:
                    resp = httpx.post(f"{API}/authenticate", data=data, files=files, timeout=120.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        score = body["voice_match_score"]
                        result = body["result"]

                        if result == "granted":
                            st.success(f"ACCESS GRANTED")
                        else:
                            st.error(f"ACCESS DENIED")

                        col1, col2, col3 = st.columns(3)
                        col1.metric("Voice Score", f"{score:.4f}")
                        col2.metric("Transcript Match", str(body["transcript_match"]))
                        col3.metric("Result", result.upper())
                        st.json(body)
                    else:
                        st.error(f"Error {resp.status_code}: {resp.json().get('detail', resp.text)}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")


# ═════════════════════════════════════════════════════════════════════════════
# CONSENT
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Consent":
    st.header("Voice Consent")
    st.markdown("Record verbal consent and generate a **cryptographically signed token**.")

    citizen_id = st.text_input("Citizen ID (UUID)", key="consent_citizen_id")
    ministry_code = st.text_input("Ministry Code", value="MOH", key="consent_ministry")
    data_scope = st.text_input("Data Scope", value="health_records", key="consent_scope")

    st.markdown("---")
    st.subheader("Consent Text")
    consent_text = f"I consent to share my {data_scope} with {ministry_code}."
    st.info(consent_text)

    consent_audio = st.file_uploader(
        "Upload your consent audio (e.g., say 'Yes, I agree')",
        type=["wav", "mp3", "ogg"],
        key="consent_audio",
    )

    if st.button("Submit Consent"):
        if not citizen_id:
            st.error("Enter a Citizen ID.")
        elif consent_audio is None:
            st.error("Upload your consent audio.")
        else:
            data = {
                "citizen_id": citizen_id,
                "ministry_code": ministry_code,
                "data_scope": data_scope,
            }
            files = [("audio_file", ("consent.wav", consent_audio.getvalue(), "audio/wav"))]
            with st.spinner("Processing consent..."):
                try:
                    resp = httpx.post(f"{API}/consent", data=data, files=files, timeout=120.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        st.success("Consent recorded and signed!")
                        st.code(body.get("digital_signature", ""), language=None)
                        st.json(body)
                    else:
                        st.error(f"Error {resp.status_code}: {resp.json().get('detail', resp.text)}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")


# ═════════════════════════════════════════════════════════════════════════════
# SERVICE ACCESS
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Service Access":
    st.header("Service Access — Health Insurance Form")
    st.markdown("Voice-driven Q&A with a simulated form.")

    citizen_id = st.text_input("Citizen ID (UUID)", key="svc_citizen_id")
    consent_token_id = st.text_input("Consent Token ID", key="svc_token_id")

    answer_audio = st.file_uploader(
        "Upload your spoken answer",
        type=["wav", "mp3", "ogg"],
        key="svc_audio",
    )

    if st.button("Submit Answer"):
        if not citizen_id or not consent_token_id:
            st.error("Citizen ID and Consent Token ID are required.")
        elif answer_audio is None:
            st.error("Upload your spoken answer.")
        else:
            data = {
                "citizen_id": citizen_id,
                "consent_token_id": consent_token_id,
            }
            files = [("audio_file", ("answer.wav", answer_audio.getvalue(), "audio/wav"))]
            with st.spinner("Processing answer..."):
                try:
                    resp = httpx.post(f"{API}/service-access", data=data, files=files, timeout=120.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        st.success("Answer recorded!")
                        st.markdown(f"**Question:** {body.get('question', 'N/A')}")
                        st.markdown(f"**Your Answer:** {body.get('transcribed_answer', 'N/A')}")
                        st.json(body)
                    else:
                        st.error(f"Error {resp.status_code}: {resp.json().get('detail', resp.text)}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")
