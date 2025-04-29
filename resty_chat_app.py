# Filename: resty_chat_app.py (Modified for Job ID in First Message)
import streamlit as st
import requests
import uuid
import json
import logging
import traceback
import os

# --- Configuration ---
LANGFLOW_BASE_URL = "http://localhost:7860"
# Flow ID should now be the one that expects input via ChatInput ONLY
FLOW_ID = "3cc6edb3-12fc-4912-a7a6-07714beba4b4" # Keep this if it's the correct flow
LANGFLOW_API_KEY = None
JOB_DATA_FILE = "sample_job_data_en.json"
# JOB_ID_COMPONENT_HANDLE is NO LONGER NEEDED if using ChatInput for Job ID

# Restworld Branding
RESTWORLD_LOGO_URL = "https://framerusercontent.com/images/24BNdNzodiF6qrG7Yp4IwSA5I.svg"
RESTWORLD_PRIMARY_COLOR = "#0b6efd"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Load Job IDs ---
def load_job_ids():
    # ... (keep load_job_ids function as before) ...
    try:
        if not os.path.exists(JOB_DATA_FILE): logger.error(f"Error: {JOB_DATA_FILE} not found."); return []
        with open(JOB_DATA_FILE, 'r') as f: data = json.load(f)
        if isinstance(data, list):
             job_ids = [item.get("job_id") for item in data if isinstance(item, dict) and "job_id" in item]; logger.info(f"Loaded Job IDs: {job_ids}"); return job_ids
        else: logger.error(f"Error: Invalid format in {JOB_DATA_FILE}"); return []
    except Exception as e: logger.error(f"Error loading job IDs: {e}"); return []

job_id_options = load_job_ids()

# --- Streamlit Page Setup ---
st.set_page_config(page_title="Resty - Chat", layout="wide", initial_sidebar_state="expanded")

# --- CSS Styling ---
st.markdown("""<style> /* Your CSS here */ </style>""", unsafe_allow_html=True)

# --- Langflow API Interaction Logic ---
# MODIFIED: Removed job_id parameter
def run_flow_debug(message: str, session_id: str) -> dict:
    """Sends input to Langflow. Job ID is expected in the *first* message."""
    api_url = f"{LANGFLOW_BASE_URL}/api/v1/run/{FLOW_ID}?stream=false"
    headers = {"Content-Type": "application/json"}
    if LANGFLOW_API_KEY: headers["x-api-key"] = LANGFLOW_API_KEY

    # MODIFIED Payload: Remove tweaks, session_id remains
    payload = {
        "input_value": message, # This will be the Job ID on the first call
        "output_type": "chat",
        "input_type": "chat",
        "session_id": session_id,
        # REMOVED tweaks
    }

    logger.info(f"--- Sending Request ---")
    logger.info(f"URL: {api_url}")
    logger.info(f"Payload: {json.dumps(payload)}")

    raw_result = {"error": "Request failed before sending"}
    try:
        # ... (rest of try/except block remains the same) ...
        response = requests.post(api_url, headers=headers, json=payload, timeout=90)
        logger.info(f"--- Received Response ---")
        logger.info(f"Status Code: {response.status_code}")
        raw_result = response.json()
        logger.info(f"Raw Response Body: {json.dumps(raw_result)}")
        response.raise_for_status()
        return raw_result

    except requests.exceptions.RequestException as e: logger.error(f"API Request Error: {e}"); raw_result = {"error": f"API connection failed: {e}"}; return raw_result
    except json.JSONDecodeError as e: logger.error(f"Failed to decode JSON response: {e}"); raw_result = {"error": "Failed to decode JSON", "status": response.status_code, "body": response.text}; return raw_result
    except Exception as e: logger.error(f"Unexpected error: {e}\n{traceback.format_exc()}"); raw_result = {"error": f"Unexpected error: {e}"}; return raw_result


# --- Function to Extract Message (Keep as before) ---
def extract_message_from_result(result: dict) -> str:
    # ... (keep extraction logic as before) ...
    if not isinstance(result, dict): return "Error: Invalid result format."
    if "error" in result: return f"Error: {result['error']}"
    try: # Nested structure
        outputs_list = result.get("outputs", [])
        if outputs_list and isinstance(outputs_list[0].get("outputs"), list):
            message_data = outputs_list[0]["outputs"][0].get("results", {}).get("message", {})
            bot_message = message_data.get("text") or message_data.get("content") or message_data.get("message")
            if bot_message: return str(bot_message)
    except Exception: pass
    try: # Simpler structure
        if isinstance(result.get("result"), dict): bot_message = result["result"].get("message", {}).get("text") or result["result"].get("message", {}).get("content")
        elif isinstance(result.get("message"), dict): bot_message = result.get("message", {}).get("text") or result.get("message", {}).get("content")
        if bot_message: return str(bot_message)
    except Exception: pass
    logger.warning(f"Could not extract primary message."); return f"```json\n{json.dumps(result, indent=2)}\n```"


# --- App UI ---
st.markdown(f'<div class="logo-container"><img src="{RESTWORLD_LOGO_URL}" alt="Restworld Logo" width="200"></div>', unsafe_allow_html=True)
st.title("Resty Chat")
st.caption("Powered by Langflow & Restworld")

# Sidebar - Keep selector for user choice, but don't use for tweaking
with st.sidebar:
    st.header("Configuration")
    if job_id_options:
         # This now controls the Job ID sent in the FIRST message
         selected_job_id = st.selectbox("Select Job ID for Assessment:", options=job_id_options, key="selected_job_id")
    else: st.warning("No Job IDs loaded."); selected_job_id = "DEFAULT_JOB_ID" # Fallback
    st.caption(f"Langflow URL: {LANGFLOW_BASE_URL}")
    st.caption(f"Flow ID: {FLOW_ID}")
    # REMOVED Job ID Handle caption
    if st.button("Clear Chat History & Restart"):
        st.session_state.messages = []
        st.session_state.session_id = f"app_session_{uuid.uuid4()}"
        st.session_state.initialized = False
        st.rerun()

# Initialize State
if "messages" not in st.session_state: st.session_state.messages = []
if "session_id" not in st.session_state: st.session_state.session_id = f"app_session_{uuid.uuid4()}"
if "initialized" not in st.session_state: st.session_state.initialized = False

# --- Initial Message Logic (MODIFIED) ---
if not st.session_state.initialized:
    # Use the job ID selected in the sidebar as the FIRST message
    initial_job_id_message = st.session_state.selected_job_id
    logger.info(f"Initializing conversation by sending Job ID: {initial_job_id_message} Session: {st.session_state.session_id}")

    # Optional: Add a visual cue to the user that we're starting with this Job ID
    # st.session_state.messages.append({"role": "system", "content": f"Starting assessment for Job ID: {initial_job_id_message}"}) # Example system message

    with st.chat_message("assistant"):
         with st.spinner("Resty is initializing..."):
             # Send the Job ID as the 'message' to trigger the flow
             raw_result = run_flow_debug(
                 message=initial_job_id_message, # SEND JOB ID HERE
                 session_id=st.session_state.session_id
                 # job_id parameter removed
             )
             response_text = extract_message_from_result(raw_result)
             # Add the actual first response from the bot to history
             st.session_state.messages.append({"role": "assistant", "content": response_text})
             st.session_state.initialized = True
             # Rerun to display the initial message correctly
             st.rerun()

# Display history
for message in st.session_state.messages:
    # Optional: Skip displaying the initial Job ID message if it was added
    # if message.get("role") == "system": continue
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat Input Logic
if prompt := st.chat_input("Your message..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.rerun()

# Handle assistant response AFTER user input has been displayed
if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    with st.chat_message("assistant"):
        with st.spinner("Resty is thinking..."):
            logger.info(f"Handling user input for Session: {st.session_state.session_id}")
            raw_result = run_flow_debug(
                message=st.session_state.messages[-1]["content"],
                session_id=st.session_state.session_id
                # job_id parameter removed
            )
            response_text = extract_message_from_result(raw_result)
            st.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})
