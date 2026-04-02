"""VeriVoice Streamlit Demo UI — calls the FastAPI backend for all four flows."""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API = f"{BACKEND_URL}/api/v1"

st.set_page_config(page_title="VeriVoice Demo", layout="wide")
st.title("VeriVoice — Voice Authentication Demo")

page = st.sidebar.radio("Navigate", ["Enroll", "Authenticate", "Consent", "Service Access", "Verify Identity (MOSIP)"])

# ── MOSIP session state badge in sidebar ─────────────────────────────────
if st.session_state.get("mosip_individual_id"):
    st.sidebar.success(f"MOSIP Verified: {st.session_state['mosip_individual_id'][:20]}...")
else:
    st.sidebar.caption("MOSIP Identity: Not verified")


# ═════════════════════════════════════════════════════════════════════════════
# ENROLL
# ═════════════════════════════════════════════════════════════════════════════
if page == "Enroll":
    if st.session_state.get("mosip_individual_id"):
        st.header("Voice Enrollment  —  MOSIP Verified")
    else:
        st.header("Voice Enrollment  —  Unverified")
    st.markdown(
        "Register a citizen with **5 voice samples**.  \n"
        "Upload your own pre-recorded audio files — say anything naturally "
        "(e.g., read a sentence, introduce yourself). No specific phrase is required."
    )

    # ── MOSIP-verified enrollment toggle ─────────────────────────────────
    use_mosip = False
    mosip_id_value = st.session_state.get("mosip_individual_id")
    if mosip_id_value:
        use_mosip = st.toggle("Use Verified MOSIP Identity for Enrollment", value=True)
        if use_mosip:
            st.info(f"Enrolling with MOSIP ID: **{mosip_id_value}**")

    with st.form("enroll_form"):
        if use_mosip:
            national_id = st.text_input(
                "National ID Number",
                placeholder="KE-123456",
                help="Still required as a local reference, even with MOSIP verification",
            )
        else:
            national_id = st.text_input("National ID Number", placeholder="KE-123456")
        language = st.selectbox("Preferred Language", ["en", "sw"], index=0)
        phone = st.text_input("Phone Number (E.164)", placeholder="+254700000000")

        st.markdown("---")
        st.subheader("Upload 5 Audio Samples (Your Own Recordings)")
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
            if use_mosip and mosip_id_value:
                data["mosip_individual_id"] = mosip_id_value

            with st.spinner("Enrolling..."):
                try:
                    resp = httpx.post(f"{API}/enroll", data=data, files=files, timeout=120.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        if body.get("identity_verified"):
                            st.success("Enrolled successfully with MOSIP-verified identity!")
                        else:
                            st.success("Enrolled successfully!")
                        st.json(body)
                    else:
                        try:
                            detail = resp.json().get("detail", resp.text)
                        except Exception:
                            detail = resp.text
                        st.error(f"Error {resp.status_code}: {detail}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}. Is it running?")


# ═════════════════════════════════════════════════════════════════════════════
# AUTHENTICATE
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Authenticate":
    if st.session_state.get("mosip_individual_id"):
        st.header("Voice Authentication  —  MOSIP Verified")
    else:
        st.header("Voice Authentication  —  Unverified")
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
                        try:
                            detail = resp.json().get("detail", resp.text)
                        except Exception:
                            detail = resp.text
                        st.error(f"Error {resp.status_code}: {detail}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")


# ═════════════════════════════════════════════════════════════════════════════
# CONSENT
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Consent":
    st.header("Voice Consent")
    st.markdown("Record verbal consent and generate a **cryptographically signed token**.")

    citizen_id = st.text_input("Citizen ID (UUID)", key="consent_citizen_id")
    lang = st.selectbox("Language", ["en", "sw"], index=0, key="consent_lang")
    ministry_code = st.text_input("Ministry Code", value="MOH", key="consent_ministry")
    data_scope = st.text_input("Data Scope", value="health_records", key="consent_scope")

    st.markdown("---")
    st.subheader("Consent Text")
    if lang == "sw":
        consent_text = f"Ninakubali kushiriki {data_scope} yangu na {ministry_code}."
        audio_prompt = "Pakia sauti yako ya idhini (mfano, sema 'Ndiyo, ninakubali')"
    else:
        consent_text = f"I consent to share my {data_scope} with {ministry_code}."
        audio_prompt = "Upload your consent audio (e.g., say 'Yes, I agree')"
    st.info(consent_text)

    consent_audio = st.file_uploader(
        audio_prompt,
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
                        try:
                            detail = resp.json().get("detail", resp.text)
                        except Exception:
                            detail = resp.text
                        st.error(f"Error {resp.status_code}: {detail}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")


# ═════════════════════════════════════════════════════════════════════════════
# SERVICE ACCESS — Health Insurance Form (3 questions + TTS read-back)
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Service Access":
    st.header("Service Access — Health Insurance Form")
    st.markdown("Voice-driven Q&A: answer **3 questions** via speech, then hear a TTS read-back for confirmation.")

    citizen_id = st.text_input("Citizen ID (UUID)", key="svc_citizen_id")
    consent_token_id = st.text_input("Consent Token ID", key="svc_token_id")
    lang = st.selectbox("Language", ["en", "sw"], index=0, key="svc_lang")

    # ── Form questions ───────────────────────────────────────────────────
    questions = {
        "en": [
            "Please say your full name.",
            "How many dependants would you like to register?",
            "Which hospital or health centre would you like as your primary facility?",
        ],
        "sw": [
            "Tafadhali sema jina lako kamili.",
            "Ungependa kusajili wategemezi wangapi?",
            "Ungependa hospitali au kituo kipi cha afya kuwa kituo chako kikuu?",
        ],
    }
    field_labels = {
        "en": ["Full Name", "Dependants", "Primary Facility"],
        "sw": ["Jina Kamili", "Wategemezi", "Kituo Kikuu"],
    }

    # Initialise session state for collected answers
    if "svc_answers" not in st.session_state:
        st.session_state["svc_answers"] = {}
    if "svc_step" not in st.session_state:
        st.session_state["svc_step"] = 0

    q_list = questions.get(lang, questions["en"])
    labels = field_labels.get(lang, field_labels["en"])
    step = st.session_state["svc_step"]

    st.markdown("---")

    # ── Show already-collected answers ───────────────────────────────────
    if st.session_state["svc_answers"]:
        st.subheader("Collected Answers" if lang == "en" else "Majibu Yaliyokusanywa")
        for idx, key in enumerate(["full_name", "dependants", "primary_facility"]):
            if key in st.session_state["svc_answers"]:
                st.success(f"**{labels[idx]}:** {st.session_state['svc_answers'][key]}")

    # ── Current question or summary ──────────────────────────────────────
    if step < len(q_list):
        st.subheader(f"Question {step + 1} of {len(q_list)}" if lang == "en" else f"Swali {step + 1} kati ya {len(q_list)}")
        st.info(q_list[step])

        answer_audio = st.file_uploader(
            "Upload your spoken answer" if lang == "en" else "Pakia jibu lako la sauti",
            type=["wav", "mp3", "ogg"],
            key=f"svc_audio_{step}",
        )

        if st.button("Submit Answer" if lang == "en" else "Wasilisha Jibu"):
            if not citizen_id or not consent_token_id:
                st.error("Citizen ID and Consent Token ID are required." if lang == "en" else "Kitambulisho cha Raia na Tokeni ya Idhini vinahitajika.")
            elif answer_audio is None:
                st.error("Upload your spoken answer." if lang == "en" else "Pakia jibu lako la sauti.")
            else:
                data = {
                    "citizen_id": citizen_id,
                    "consent_token_id": consent_token_id,
                    "question_index": str(step),
                }
                files = [("audio_file", ("answer.wav", answer_audio.getvalue(), "audio/wav"))]
                with st.spinner("Processing..." if lang == "en" else "Inachakatwa..."):
                    try:
                        resp = httpx.post(f"{API}/service-access", data=data, files=files, timeout=120.0)
                        if resp.status_code == 200:
                            body = resp.json()
                            field_key = body["field_key"]
                            answer = body["transcribed_answer"]
                            raw = body.get("raw_transcription", answer)

                            st.session_state["svc_answers"][field_key] = answer

                            # Show transcription result
                            st.success(f"**{labels[step]}:** {answer}")
                            if raw != answer:
                                st.caption(f"Raw transcription: {raw}")

                            st.session_state["svc_step"] = step + 1
                            st.rerun()
                        else:
                            try:
                                detail = resp.json().get("detail", resp.text)
                            except Exception:
                                detail = resp.text
                            st.error(f"Error {resp.status_code}: {detail}")
                    except httpx.ConnectError:
                        st.error(f"Cannot connect to backend at {BACKEND_URL}.")

    else:
        # ── All 3 questions answered — show summary + TTS read-back ──────
        answers = st.session_state["svc_answers"]
        st.subheader("Form Summary" if lang == "en" else "Muhtasari wa Fomu")

        col1, col2, col3 = st.columns(3)
        col1.metric(labels[0], answers.get("full_name", "—"))
        col2.metric(labels[1], answers.get("dependants", "—"))
        col3.metric(labels[2], answers.get("primary_facility", "—"))

        # Request TTS read-back from backend
        if st.button("Generate Read-back" if lang == "en" else "Tengeneza Muhtasari wa Sauti"):
            data = {
                "citizen_id": citizen_id,
                "consent_token_id": consent_token_id,
                "full_name": answers.get("full_name", ""),
                "dependants": answers.get("dependants", ""),
                "primary_facility": answers.get("primary_facility", ""),
                "language": lang,
            }
            with st.spinner("Generating summary audio..." if lang == "en" else "Inatengeneza sauti..."):
                try:
                    resp = httpx.post(f"{API}/service-access/summary", data=data, timeout=60.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        st.info(body["summary_text"])
                        audio_path = body.get("audio_url")
                        if audio_path:
                            try:
                                st.audio(audio_path)
                            except Exception:
                                st.caption(f"Audio saved at: {audio_path}")
                    else:
                        try:
                            detail = resp.json().get("detail", resp.text)
                        except Exception:
                            detail = resp.text
                        st.error(f"Error {resp.status_code}: {detail}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")

        # Reset button to start over
        if st.button("Start New Form" if lang == "en" else "Anza Fomu Mpya"):
            st.session_state["svc_answers"] = {}
            st.session_state["svc_step"] = 0
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# VERIFY IDENTITY (MOSIP e-Signet)
# ═════════════════════════════════════════════════════════════════════════════
elif page == "Verify Identity (MOSIP)":
    st.header("Verify Identity via MOSIP e-Signet")
    st.markdown(
        "Authenticate against the **MOSIP national ID system** using biometrics "
        "(fingerprint, iris, or face) via the e-Signet OpenID Connect flow."
    )

    # ── Handle OIDC callback (code + state in query params) ─────────────
    qp = st.query_params
    oidc_code = qp.get("code")
    oidc_state = qp.get("state")

    if oidc_code and oidc_state:
        # We've been redirected back from e-Signet with an authorization code.
        # Exchange it for a verified MOSIP identity via the backend callback.
        st.info("Processing e-Signet callback...")
        try:
            resp = httpx.get(
                f"{API}/mosip/callback",
                params={"code": oidc_code, "state": oidc_state},
                timeout=30.0,
            )
            if resp.status_code == 200:
                identity = resp.json()
                st.session_state["mosip_individual_id"] = identity["mosip_individual_id"]
                st.session_state["mosip_identity_verified"] = identity["identity_verified"]
                # Clear query params so a page refresh doesn't re-trigger
                st.query_params.clear()
                st.rerun()
            else:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                st.error(f"e-Signet callback failed ({resp.status_code}): {detail}")
                st.query_params.clear()
        except httpx.ConnectError:
            st.error(f"Cannot connect to backend at {BACKEND_URL}.")

    # ── Show current verification status ────────────────────────────────
    if st.session_state.get("mosip_individual_id"):
        st.success("Identity Confirmed")
        col1, col2 = st.columns(2)
        col1.metric("MOSIP Individual ID", st.session_state["mosip_individual_id"])
        col2.metric("Status", "Verified")

        st.markdown("---")

        # ── Link to existing citizen ────────────────────────────────────
        st.subheader("Link to Existing Citizen")
        link_citizen_id = st.text_input(
            "Citizen ID (UUID) to link",
            key="mosip_link_citizen_id",
            placeholder="550e8400-e29b-41d4-a716-446655440000",
        )
        if st.button("Link Identity"):
            if not link_citizen_id:
                st.error("Enter a Citizen ID to link.")
            else:
                try:
                    resp = httpx.post(
                        f"{API}/mosip/link",
                        json={
                            "citizen_id": link_citizen_id,
                            "mosip_individual_id": st.session_state["mosip_individual_id"],
                        },
                        timeout=30.0,
                    )
                    if resp.status_code == 200:
                        body = resp.json()
                        st.success(f"Identity linked to citizen {body['citizen_id']}!")
                        st.json(body)
                    else:
                        try:
                            detail = resp.json().get("detail", resp.text)
                        except Exception:
                            detail = resp.text
                        st.error(f"Error {resp.status_code}: {detail}")
                except httpx.ConnectError:
                    st.error(f"Cannot connect to backend at {BACKEND_URL}.")

        # ── Clear verification ──────────────────────────────────────────
        st.markdown("---")
        if st.button("Clear Verification"):
            del st.session_state["mosip_individual_id"]
            if "mosip_identity_verified" in st.session_state:
                del st.session_state["mosip_identity_verified"]
            st.rerun()

    else:
        # ── Initiate e-Signet OIDC flow ─────────────────────────────────
        st.markdown("Click below to verify your identity via MOSIP e-Signet.")
        if st.button("Verify with MOSIP"):
            try:
                resp = httpx.get(f"{API}/mosip/authorize", timeout=10.0)
                if resp.status_code == 200:
                    body = resp.json()
                    st.session_state["mosip_oidc_state"] = body["state"]
                    st.markdown(
                        f'<a href="{body["authorize_url"]}" target="_self">'
                        f'<button style="background-color:#4CAF50;color:white;'
                        f'padding:10px 24px;border:none;border-radius:4px;'
                        f'cursor:pointer;font-size:16px;">'
                        f'Open MOSIP e-Signet Login</button></a>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.error(f"Error {resp.status_code}: {resp.text}")
            except httpx.ConnectError:
                st.error(f"Cannot connect to backend at {BACKEND_URL}.")
