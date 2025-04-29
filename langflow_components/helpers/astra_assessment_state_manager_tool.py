# Filename: astra_assessment_state_manager_tool.py (V1.1 - Fixed Caching Logic)
import os
from typing import Optional, List, Dict, Any, ClassVar

from langflow.base.langchain_utilities.model import LCToolComponent
from langchain_core.tools import BaseTool, StructuredTool, Tool
from langchain.pydantic_v1 import BaseModel, Field
from langflow.io import StrInput, SecretStrInput, HandleInput # Use appropriate inputs
from langflow.logging import logger # Use langflow logger

# Astra DB specific imports
from astrapy import Collection, DataAPIClient, Database
from astrapy.admin import parse_api_endpoint
import logging # Standard logging
import traceback

# --- Logging Configuration ---
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
astra_logger = logging.getLogger("AstraDBAssessmentStateManager")

# --- Constants ---
DEFAULT_JOB_QUESTIONS = ["Default Q1?", "Default Q2?", "Default Q3?"]
COMPLETION_SIGNAL = "[ASSESSMENT_COMPLETE]"
ASTRA_ID_FIELD = "_id"

# --- Pydantic Input Schema ---
class AstraDBAssessmentToolInputSchema(BaseModel):
    """Input schema for the Astra DB Assessment State Manager Tool."""
    session_id_input: str = Field(..., description="Unique identifier for the session. This will be the document ID.")
    action: str = Field(..., description="Action: 'INITIALIZE_WITH_QUESTIONS', 'GET_CURRENT_QUESTION', 'RECORD_ANSWER_AND_GET_NEXT'")
    answer_payload: Optional[str] = Field(None, description="User's answer text (required for RECORD_ANSWER_AND_GET_NEXT).")
    questions_payload: Optional[List[str]] = Field(None, description="List of questions (required for INITIALIZE_WITH_QUESTIONS).")

# --- Main Tool Component ---
class AstraDBAssessmentStateManagerTool(LCToolComponent):
    display_name = "Astra DB Assessment State Manager"
    description = "Manages assessment state using an Astra DB collection for persistence."
    icon = "AstraDB"
    name = "AstraDBAssessmentStateManagerTool"
    beta: bool = True

    inputs = [
        StrInput(
            name="keyspace",
            display_name="Keyspace Name",
            info="The name of the keyspace within Astra where the state collection exists.",
            value="default_keyspace",
            advanced=False,
        ),
        StrInput(
            name="collection_name",
            display_name="State Collection Name",
            info="The name of the collection within Astra DB where assessment state will be stored.",
            value="assessment_state",
            required=True,
        ),
        SecretStrInput(
            name="token",
            display_name="Astra DB Application Token",
            info="Authentication token for accessing Astra DB.",
            value="ASTRA_DB_APPLICATION_TOKEN",
            required=True,
        ),
        SecretStrInput(
            name="api_endpoint",
            display_name="API Endpoint / Database ID",
            info="API endpoint URL or Database ID for the Astra DB service.",
            value="ASTRA_DB_API_ENDPOINT",
            required=True,
        ),
    ]

    # Astra DB Client Caching
    _cached_collection: Collection | None = None
    # Store the parameters used for the cached collection
    _cached_token_val: str | None = None
    _cached_api_endpoint_val: str | None = None
    _cached_keyspace_val: str | None = None
    _cached_collection_name_val: str | None = None


    def _build_collection(self) -> Collection: # Added return type hint
        """Builds and caches the Astra DB Collection object."""
        # Ensure token and endpoint values are unwrapped correctly for comparison/use
        current_token_val = self.token if isinstance(self.token, str) else self.token.get_secret_value()
        current_api_endpoint_val = self.api_endpoint if isinstance(self.api_endpoint, str) else self.api_endpoint.get_secret_value()

        # Check cache first using cached parameters
        if (self._cached_collection is not None and
            self._cached_token_val == current_token_val and
            self._cached_api_endpoint_val == current_api_endpoint_val and
            self._cached_keyspace_val == self.keyspace and
            self._cached_collection_name_val == self.collection_name):
             astra_logger.debug("Using cached Astra DB collection object.")
             return self._cached_collection

        # Cache is invalid or doesn't exist, build new connection
        astra_logger.debug(f"Building Astra DB collection object for endpoint: {current_api_endpoint_val}, keyspace: {self.keyspace}, collection: {self.collection_name}")
        try:
            environment = parse_api_endpoint(current_api_endpoint_val).environment

            # Initialize client and database
            # Use the unwrapped token value
            client = DataAPIClient(current_token_val, environment=environment)
            db = client.get_database(current_api_endpoint_val, keyspace=self.keyspace)
            collection = db.get_collection(self.collection_name)

            astra_logger.info(f"Successfully connected to Astra DB Collection: {self.collection_name}")

            # Update cache only on successful connection
            self._cached_collection = collection
            self._cached_token_val = current_token_val
            self._cached_api_endpoint_val = current_api_endpoint_val
            self._cached_keyspace_val = self.keyspace
            self._cached_collection_name_val = self.collection_name

            return self._cached_collection
        except Exception as e:
            # Clear cache on error
            self._cached_collection = None
            self._cached_token_val = None
            self._cached_api_endpoint_val = None
            self._cached_keyspace_val = None
            self._cached_collection_name_val = None
            astra_logger.error(f"Error building Astra DB collection: {e}", exc_info=True)
            raise ValueError(f"Error connecting to Astra DB: {e}") from e

    # --- State Helper Methods (Astra DB Implementation - unchanged) ---
    def _get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the state document for a given session_id from Astra DB."""
        if not session_id: astra_logger.error("Attempted to get state with empty session_id."); return None
        try:
            collection = self._build_collection()
            astra_logger.debug(f"Attempting to find state for session_id (document _id): {session_id}")
            state_doc = collection.find_one({"_id": session_id})
            if state_doc:
                astra_logger.debug(f"Found state for session {session_id}: {state_doc}")
                state_doc.setdefault("current_question_index", 0)
                state_doc.setdefault("collected_answers", {})
                state_doc.setdefault("assessment_complete", False)
                state_doc.setdefault("job_questions", DEFAULT_JOB_QUESTIONS)
                return state_doc
            else: astra_logger.debug(f"No state found for session {session_id}."); return None
        except Exception as e: astra_logger.error(f"Error getting state for session {session_id} from Astra DB: {e}", exc_info=True); return None

    def _create_or_update_session_state(self, session_id: str, state_data: Dict[str, Any]) -> bool:
        """Creates or replaces the state document for a session_id in Astra DB."""
        if not session_id: astra_logger.error("Attempted to update state with empty session_id."); return False
        try:
            collection = self._build_collection()
            document_to_save = {**state_data, "_id": session_id}
            astra_logger.debug(f"Attempting to save state for session {session_id}: {document_to_save}")
            result = collection.find_one_and_replace( filter={"_id": session_id}, replacement=document_to_save, upsert=True )
            astra_logger.info(f"Successfully saved state for session {session_id}. Upsert result: {result}")
            return True
        except Exception as e: astra_logger.error(f"Error saving state for session {session_id} to Astra DB: {e}", exc_info=True); return False

    # --- Tool Execution Logic (Instance Method - unchanged) ---
    def _execute_state_action( self, session_id_input: str, action: str, answer_payload: Optional[str] = None, questions_payload: Optional[List[str]] = None ) -> str:
        session_id = session_id_input
        astra_logger.info(f"Tool Execute START | Session: {session_id} | Action: {action}")
        astra_logger.debug(f"Session {session_id}: Action='{action}', Answer Payload='{answer_payload}', Questions Payload Provided={'Yes' if questions_payload else 'No'}")

        if not session_id: astra_logger.error("Tool received empty session_id_input."); return "Error: Session ID was empty."

        try:
            # ACTION: INITIALIZE
            if action == "INITIALIZE_WITH_QUESTIONS":
                if questions_payload is None: astra_logger.error(f"Session {session_id}: INITIALIZE called without questions_payload."); return "Error: questions_payload missing."
                if not isinstance(questions_payload, list): astra_logger.error(f"Session {session_id}: questions_payload was not a list."); return "Error: questions_payload must be a list of strings."
                initial_state = { "current_question_index": 0, "collected_answers": {}, "assessment_complete": False, "job_questions": questions_payload or DEFAULT_JOB_QUESTIONS }
                astra_logger.info(f"Session {session_id}: Initializing/Resetting state with {len(initial_state['job_questions'])} questions.")
                astra_logger.debug(f"Session {session_id}: Initial state details: {initial_state}")
                if self._create_or_update_session_state(session_id, initial_state):
                    first_question = initial_state["job_questions"][0] if initial_state["job_questions"] else None
                    if first_question: astra_logger.info(f"Session {session_id}: Initialization successful. Returning first question."); return first_question
                    else: astra_logger.error(f"Session {session_id}: Initialization done but question list is empty."); return "Error: Initialized empty list."
                else: astra_logger.error(f"Session {session_id}: Failed to save initial state to Astra DB."); return "Error: Failed save state after init."

            current_state = self._get_session_state(session_id)
            if current_state is None and action != "INITIALIZE_WITH_QUESTIONS": astra_logger.error(f"Session {session_id}: State not found for action '{action}'. Please initialize first."); return f"Error: Session state not found for {session_id}. Please start over or initialize."

            # ACTION: GET CURRENT QUESTION
            elif action == "GET_CURRENT_QUESTION":
                if current_state["assessment_complete"]: astra_logger.info(f"Session {session_id}: GET_CURRENT called, assessment already complete. Returning signal."); return COMPLETION_SIGNAL
                idx = current_state["current_question_index"]; questions = current_state["job_questions"]
                if not isinstance(questions, list) or not questions: astra_logger.error(f"Session {session_id}: Invalid questions list in retrieved state: {questions}"); return "Error: Questions invalid state."
                if idx < len(questions): astra_logger.info(f"Session {session_id}: Returning current question index {idx}."); return questions[idx]
                else: astra_logger.warning(f"Session {session_id}: Index {idx} out of bounds ({len(questions)} questions), marking complete now."); current_state["assessment_complete"] = True; self._create_or_update_session_state(session_id, current_state); return COMPLETION_SIGNAL

            # ACTION: RECORD_ANSWER_AND_GET_NEXT
            elif action == "RECORD_ANSWER_AND_GET_NEXT":
                if answer_payload is None: astra_logger.error(f"Session {session_id}: RECORD_ANSWER called without answer_payload."); return "Error: answer_payload missing."
                if current_state["assessment_complete"]: astra_logger.info(f"Session {session_id}: RECORD_ANSWER called, assessment already complete. Returning signal."); return COMPLETION_SIGNAL
                idx = current_state["current_question_index"]; questions = current_state["job_questions"]
                if not isinstance(questions, list) or not questions: astra_logger.error(f"Session {session_id}: Invalid questions list in state during RECORD_ANSWER: {questions}"); return "Error: Questions invalid state."
                astra_logger.info(f"Session {session_id}: Recording answer '{answer_payload}' for question index {idx}."); astra_logger.debug(f"Session {session_id}: State BEFORE record attempt: {current_state}")
                if idx < len(questions):
                    current_state["collected_answers"][str(idx)] = answer_payload; next_idx = idx + 1; current_state["current_question_index"] = next_idx
                    result_signal = "";
                    if next_idx >= len(questions): current_state["assessment_complete"] = True; astra_logger.info(f"Session {session_id}: Assessment marked complete after recording final answer (idx {idx})."); result_signal = COMPLETION_SIGNAL
                    else: result_signal = questions[next_idx]; astra_logger.info(f"Session {session_id}: Recorded answer for Q{idx}, next index is {next_idx}. Returning next question.")
                    astra_logger.debug(f"Session {session_id}: State BEFORE final save attempt: {current_state}")
                    save_success = self._create_or_update_session_state(session_id, current_state)
                    astra_logger.info(f"Session {session_id}: State save attempt result after recording answer: {save_success}")
                    if not save_success: astra_logger.error(f"Session {session_id}: CRITICAL - Failed to save state update to Astra DB after recording answer for index {idx}."); return "Error: Failed save state after record."
                    astra_logger.info(f"Session {session_id}: Successfully saved state. Returning signal: {result_signal}"); return result_signal
                else: astra_logger.warning(f"Session {session_id}: RECORD_ANSWER called when index ({idx}) was already >= question count ({len(questions)}). Marking complete."); current_state["assessment_complete"] = True; self._create_or_update_session_state(session_id, current_state); return COMPLETION_SIGNAL
            else: astra_logger.error(f"Session {session_id}: Unknown action received: '{action}'."); return f"Error: Unknown action '{action}'."

        except Exception as e: astra_logger.error(f"Tool Exception | Session: {session_id} | Action: {action} | Error: {e}", exc_info=True); return f"Error: Internal tool error. Details: {e}"
        finally: astra_logger.info(f"Tool Execute END | Session: {session_id} | Action: {action}")

    # --- Build Tool Method (unchanged) ---
    def build_tool(self) -> Tool:
        tool_name = "manage_assessment_state_astra_v1"
        astra_logger.info(f"Attempting to build {self.display_name} as tool '{tool_name}'...")
        try:
            self._build_collection() # Ensure connection works before returning tool
            assessment_tool = StructuredTool.from_function( name=tool_name, func=self._execute_state_action, args_schema=AstraDBAssessmentToolInputSchema, description="Manages assessment state using Astra DB. Actions: INITIALIZE_WITH_QUESTIONS, GET_CURRENT_QUESTION, RECORD_ANSWER_AND_GET_NEXT.", )
            self.status = f"{self.display_name} built successfully as '{tool_name}'."
            astra_logger.info(self.status)
            return assessment_tool
        except Exception as e:
            astra_logger.error(f"CRITICAL ERROR in build_tool for {self.display_name}: {e}", exc_info=True)
            error_tool = Tool( name=f"build_error_{tool_name}", func=lambda x: f"Error building Astra DB state tool: {e}", description="Tool failed to build due to Astra DB connection or setup issues." )
            self.status = f"Error building {self.display_name}: {e}"; return error_tool
