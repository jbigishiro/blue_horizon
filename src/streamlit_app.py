"""
Blue Horizon concierge — Streamlit frontend.
Its job is rendering the conversation
and forwarding messages to the API.

Run:
    cd frontend
    streamlit run streamlit_app.py

Requires the FastAPI backend running separately :
    cd app
    uvicorn main:app --reload --port 8000
"""

import os
import uuid
from datetime import datetime, date
import requests
import streamlit as st


API_BASE_URL = ("http://localhost:8000").rstrip("/")

st.set_page_config(page_title="Blue Horizon AI Concierge", page_icon="🏨")


if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "customer_id" not in st.session_state:
    st.session_state.customer_id = None

if "messages" not in st.session_state:
    st.session_state.messages = []  
if "needs_confirmation" not in st.session_state:
    st.session_state.needs_confirmation = False


# --- Sidebar -------------------------------------------------------------

with st.sidebar:
    st.header("Blue Horizon")

    customer_id_input = st.number_input(
        "Guest ID ",
        min_value=0,
        value=st.session_state.customer_id or 0,
        #step=1,
        help=(
            "Stands in for a real authentication system, which doesn't "
            "exist yet. Booking or cancelling requires a guest ID here — "
            "0 means 'not logged in'."
        ),
    )
    st.session_state.customer_id = customer_id_input if customer_id_input > 0 else None

    if st.session_state.customer_id is None:
        st.warning("⚠️ Not signed in. Enter a Guest ID above to book or cancel a stay.")
    else:
        st.success(f"✅ Signed in as Guest #{st.session_state.customer_id}")

    st.divider()

    if st.button("🔄 New conversation"):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.needs_confirmation = False
        st.rerun()

    
    st.caption(f"Copyright {datetime.now().year}")


# --- Main chat area --------------------------------------------------

st.title("🏨 Welcome to Blue Horizon AI Concierge")
st.caption(date.today().strftime("%B %d, %Y"))
st.caption("Ask about rooms, amenities, policies — or book a stay.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"].replace("$", "\\$"))

if st.session_state.needs_confirmation:
    st.info("💬 Waiting on your confirmation above — reply yes or no.")


def send_message(text: str):
    st.session_state.messages.append({"role": "user", "content": text})

    try:
        response = requests.post(
            f"{API_BASE_URL}/chat",
            json={
                "session_id": st.session_state.session_id,
                "message": text,
                "customer_id": st.session_state.customer_id,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        answer = data["answer"]
        st.session_state.needs_confirmation = data.get("needs_confirmation", False)

    except requests.exceptions.ConnectionError:
        answer = (
            "⚠️ I can't reach the concierge service right now. Is the "
            "backend running? (`uvicorn main:app --reload --port 8000`)"
        )
        st.session_state.needs_confirmation = False
    except requests.exceptions.RequestException as e:
        answer = f"⚠️ Something went wrong talking to the concierge service: {e}"
        st.session_state.needs_confirmation = False

    st.session_state.messages.append({"role": "assistant", "content": answer})


user_input = st.chat_input("Ask about your stay, amenities, or book a room...")
if user_input:
    send_message(user_input)
    st.rerun()