import streamlit as st
import pandas as pd
import random
import os
import gspread # Make sure this is in your requirements.txt

# --- Configuration ---
# Replace with your Google Sheet ID and the exact name of the worksheet
GOOGLE_SHEET_ID = "1mLse9BqCBz9Jw5jqBSChb0a-OwDKyso-mCkauFrl22E" # <--- YOUR SHEET ID IS HERE!
WORKSHEET_NAME = "Sheet1" # <--- IMPORTANT: Update this if your sheet tab is named differently

# --- Data Loading (Cached for performance and Sheet Interaction) ---
@st.cache_data(ttl=600) # Cache for 10 minutes to avoid re-reading sheet too often
def load_data_from_gsheets():
    """Loads the data from the specified Google Sheet."""
    conn = st.connection("gsheets", type="pandas")
    try:
        df = conn.read(spreadsheet=GOOGLE_SHEET_ID, worksheet=WORKSHEET_NAME, ttl=5)
        # Ensure 'Attempted', 'Incorrect attempt', and 'Question No.' columns are numeric, fill NaN with 0
        df['Attempted'] = pd.to_numeric(df['Attempted'], errors='coerce').fillna(0).astype(int)
        df['Incorrect attempt'] = pd.to_numeric(df['Incorrect attempt'], errors='coerce').fillna(0).astype(int)
        # Assuming 'Question No.' is already an integer or can be converted
        if 'Question No.' in df.columns:
            df['Question No.'] = pd.to_numeric(df['Question No.'], errors='coerce').fillna(0).astype(int)
        else:
            st.warning("Column 'Question No.' not found in your Google Sheet. Questions will not display their number.")
            df['Question No.'] = df.index + 1 # Fallback to DataFrame index + 1
            
        return df
    except Exception as e:
        st.error(f"Error loading data from Google Sheet: {e}. Please check your Sheet ID, Worksheet Name, and service account permissions.")
        st.stop() # Stop the app if data loading fails

# Function to update a specific cell in the Google Sheet
def update_gsheet_cell(row_index, col_name, value):
    # This function uses gspread directly as st.connection().write() for arbitrary cell updates is not straightforward.
    try:
        # Authenticate using st.secrets
        gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
        sh = gc.open_by_id(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(WORKSHEET_NAME)
    except Exception as e:
        st.error(f"Error authenticating with Google Sheets or opening sheet: {e}. Check your `gcp_service_account` secret and Sheet ID/Name.")
        return # Exit if authentication fails

    # Find column index (1-based)
    headers = worksheet.row_values(1) # Get first row to find headers
    try:
        col_index_to_update = headers.index(col_name) + 1 # Convert to 1-based index
    except ValueError:
        st.error(f"Column '{col_name}' not found in Google Sheet headers. Cannot update.")
        return

    # Update cell (row_index + 2 because gspread is 1-based and accounts for header)
    # The original_df_index (from pandas df.index) is 0-based.
    # Google Sheet rows are 1-based. Plus, if row 1 is headers, data starts at row 2.
    # So, a 0-indexed row in DataFrame is row 2 in Sheet. A 1-indexed row is row 3 etc.
    gsheet_row_number = row_index + 2
    try:
        worksheet.update_cell(gsheet_row_number, col_index_to_update, value)
    except Exception as e:
        st.error(f"Error updating cell in Google Sheet at row {gsheet_row_number}, col {col_index_to_update}: {e}. Check sheet permissions for service account.")

# --- Session State Initialization ---
if 'quiz_stage' not in st.session_state:
    st.session_state.quiz_stage = 'chapter_selection'
    st.session_state.data_df = load_data_from_gsheets() # Load data from GSheets
    st.session_state.selected_chapters = []
    st.session_state.practice_mode = None
    st.session_state.num_questions_to_ask = 0
    st.session_state.quiz_questions = pd.DataFrame()
    st.session_state.current_question_idx = 0
    st.session_state.correct_answers_count = 0
    st.session_state.total_questions_asked = 0
    # Initialize 'Attempted' and 'Incorrect attempt' counts from loaded data_df
    st.session_state.attempted_counts = st.session_state.data_df['Attempted'].to_dict()
    st.session_state.incorrect_attempt_counts = st.session_state.data_df['Incorrect attempt'].to_dict()

# --- Helper Functions (Adapted for Streamlit) ---

def parse_chapter_input(df, chapter_input_str):
    """Parses chapter input string and returns a list of selected chapter numbers."""
    selected_chapters = []
    all_available_chapters = sorted(df['Chapter'].unique().tolist())

    if chapter_input_str == 'all':
        selected_chapters = all_available_chapters
    elif '-' in chapter_input_str:
        try:
            start, end = map(int, chapter_input_str.split('-'))
            selected_chapters = [ch for ch in all_available_chapters if start <= ch <= end]
        except ValueError:
            st.error("Invalid range format. Please use 'start-end' (e.g., '2-5').")
            return []
    else:
        try:
            selected_chapters = [int(ch.strip()) for ch in chapter_input_str.split(',')]
            if not all(ch in all_available_chapters for ch in selected_chapters):
                st.error("Some entered chapters do not exist. Please check the chapter numbers.")
                return []
        except ValueError:
            st.error("Invalid input format. Please use comma-separated numbers (e.g., '1,2,4') or 'all' or 'start-end'.")
            return []
    return selected_chapters

def get_question_counts_streamlit(df, selected_chapters, attempted_counts, incorrect_attempt_counts):
    """Calculates question counts for display in Streamlit."""
    filtered_df = df[df['Chapter'].isin(selected_chapters)].copy()
    total_selected_questions = len(filtered_df)
    
    incorrect_count = sum(1 for idx in filtered_df.index if incorrect_attempt_counts.get(idx, 0) > 0)
    new_count = sum(1 for idx in filtered_df.index if attempted_counts.get(idx, 0) == 0)

    return total_selected_questions, incorrect_count, new_count

def get_questions_for_mode_streamlit(df, selected_chapters, mode, attempted_counts, incorrect_attempt_counts):
    """Filters questions based on the selected mode for Streamlit."""
    filtered_df = df[df['Chapter'].isin(selected_chapters)].copy()
    
    if mode == 'normal':
        return filtered_df
    elif mode == 'incorrect':
        incorrect_q_indices = [idx for idx, count in incorrect_attempt_counts.items() if count > 0]
        return filtered_df[filtered_df.index.isin(incorrect_q_indices)]
    elif mode == 'new':
        new_q_indices = [idx for idx, count in attempted_counts.items() if count == 0]
        return filtered_df[filtered_df.index.isin(new_q_indices)]

# --- Quiz UI Logic ---

st.title("Practice Exam Quiz")

if st.session_state.quiz_stage == 'chapter_selection':
    st.header("A1. Select Chapters")
    chapter_input = st.text_input(
        "Enter chapters (e.g., '1,2,4' for chapters 1, 2, 4; '2-5' for chapters 2 to 5; 'all' for all chapters):"
    )
    if st.button("Confirm Chapters"):
        selected_chs = parse_chapter_input(st.session_state.data_df, chapter_input)
        if selected_chs:
            st.session_state.selected_chapters = selected_chs
            st.session_state.quiz_stage = 'mode_selection'
            st.rerun() # Rerun to move to next stage

elif st.session_state.quiz_stage == 'mode_selection':
    st.header("A2. Choose Practice Mode")
    st.write(f"You have selected chapters: {st.session_state.selected_chapters}")

    total_selected_questions, incorrect_count, new_count = get_question_counts_streamlit(
        st.session_state.data_df, 
        st.session_state.selected_chapters,
        st.session_state.attempted_counts,
        st.session_state.incorrect_attempt_counts
    )

    st.markdown("--- **Question Availability in Selected Chapters** ---")
    st.write(f"**a) Total questions:** {total_selected_questions}")
    st.write(f"**b) Incorrectly answered questions:** {incorrect_count}")
    st.write(f"**c) New questions (not attempted yet):** {new_count}")
    st.markdown("---")

    mode_options = {}
    if total_selected_questions > 0:
        mode_options['a - Normal Practice (all questions)'] = 'normal'
    if incorrect_count > 0:
        mode_options['b - Focus on incorrect questions'] = 'incorrect'
    if new_count > 0:
        mode_options['c - New questions'] = 'new'

    if not mode_options:
        st.warning("No questions available for any practice mode based on your selection. Please adjust chapters.")
        if st.button("Go back to Chapter Selection"):
            st.session_state.quiz_stage = 'chapter_selection'
            st.rerun()
    else:
        selected_display_mode = st.radio(
            "Choose a practice mode:",
            list(mode_options.keys())
        )
        if st.button("Select Mode"):
            st.session_state.practice_mode = mode_options[selected_display_mode]
            st.session_state.quiz_stage = 'num_questions_selection'
            st.rerun()

elif st.session_state.quiz_stage == 'num_questions_selection':
    st.header("A3. How many questions?")
    
    quiz_pool_df = get_questions_for_mode_streamlit(
        st.session_state.data_df, 
        st.session_state.selected_chapters, 
        st.session_state.practice_mode,
        st.session_state.attempted_counts,
        st.session_state.incorrect_attempt_counts
    )
    max_questions_available = len(quiz_pool_df)

    if max_questions_available == 0:
        st.warning("No questions available for the selected mode. Please choose another mode.")
        if st.button("Go back to Mode Selection"):
            st.session_state.quiz_stage = 'mode_selection'
            st.rerun()
    else:
        num_questions = st.number_input(
            f"How many questions do you want (1 min, {max_questions_available} max)?",
            min_value=1,
            max_value=max_questions_available,
            value=min(10, max_questions_available), # Default to 10 or max available
            step=1
        )

        if st.button("Start Quiz"):
            if num_questions > 0 and num_questions <= max_questions_available:
                st.session_state.num_questions_to_ask = num_questions
                # Randomly pick questions
                st.session_state.quiz_questions = quiz_pool_df.sample(n=num_questions) if num_questions < max_questions_available else quiz_pool_df
                st.session_state.total_questions_asked = len(st.session_state.quiz_questions)
                st.session_state.current_question_idx = 0
                st.session_state.correct_answers_count = 0
                st.session_state.quiz_stage = 'quiz_in_progress'
                st.rerun()
            else:
                st.error(f"Please enter a number between 1 and {max_questions_available}.")


elif st.session_state.quiz_stage == 'quiz_in_progress':
    if st.session_state.current_question_idx < st.session_state.total_questions_asked:
        current_q_data = st.session_state.quiz_questions.iloc[st.session_state.current_question_idx]
        original_df_index = current_q_data.name # Get the original index from the main DataFrame

        st.header(f"B1. Question {st.session_state.current_question_idx + 1} of {st.session_state.total_questions_asked}")
        st.write(f"Questions remaining: {st.session_state.total_questions_asked - (st.session_state.current_question_idx + 1)}")
        st.markdown("---")

        # Display Question Number if available
        question_display_text = ""
        if 'Question No.' in current_q_data and current_q_data['Question No.'] > 0:
            question_display_text = f"[Qn {current_q_data['Question No.']}] "
        
        st.markdown(f"**{question_display_text}Question:** {current_q_data['Question']}")

        options = {
            'a': current_q_data['Option A'],
            'b': current_q_data['Option B'],
            'c': current_q_data['Option C'],
            'd': current_q_data['Option D']
        }
        
        user_choice_key = st.radio(
            "Select your answer:",
            list(options.keys()),
            format_func=lambda k: f"{k.upper()}) {options[k]}",
            key=f"question_{st.session_state.current_question_idx}" # Unique key for radio buttons
        )

        if st.button("Submit Answer", key=f"submit_btn_{st.session_state.current_question_idx}"):
            correct_answer_key = current_q_data['Correct Answer'].lower()
            
            # Update attempted count in session state and then in Google Sheet
            st.session_state.attempted_counts[original_df_index] += 1
            update_gsheet_cell(original_df_index, 'Attempted', st.session_state.attempted_counts[original_df_index])

            if user_choice_key == correct_answer_key:
                st.success("Correct!")
                st.session_state.correct_answers_count += 1
                # Reset incorrect count in session state and then in Google Sheet
                st.session_state.incorrect_attempt_counts[original_df_index] = 0
                update_gsheet_cell(original_df_index, 'Incorrect attempt', st.session_state.incorrect_attempt_counts[original_df_index])
            else:
                st.error(f"Incorrect. The correct answer was {correct_answer_key.upper()}.")
                st.warning(f"Reason: {current_q_data['Reason']}")
                # Increment incorrect count in session state and then in Google Sheet
                st.session_state.incorrect_attempt_counts[original_df_index] += 1
                update_gsheet_cell(original_df_index, 'Incorrect attempt', st.session_state.incorrect_attempt_counts[original_df_index])
            
            # Move to next question after feedback
            st.session_state.current_question_idx += 1
            st.button("Next Question", key=f"next_btn_{st.session_state.current_question_idx}", on_click=st.rerun) # Force rerun to show next question or finish

    else: # Quiz finished
        st.session_state.quiz_stage = 'quiz_finished'
        st.rerun()

elif st.session_state.quiz_stage == 'quiz_finished':
    correct = st.session_state.correct_answers_count
    total = st.session_state.total_questions_asked

    if total == 0:
        percentage = 0
    else:
        percentage = (correct / total) * 100

    score_text = f"{correct} / {total}"
    percentage_text = f"({percentage:.2f}%)"

    if percentage >= 80:
        color_style = "color:darkgreen; font-weight:bold;"
    elif percentage >= 70:
        color_style = "color:darkorange; font-weight:bold;"
    else:
        color_style = "color:red; font-weight:bold;"

    st.markdown(f"<h2 style='{color_style}'>Quiz Complete! Your Score: {score_text} {percentage_text}</h2>", unsafe_allow_html=True)

    if st.button("Start New Quiz"):
        # Reload data to get latest persistent counts from GSheets for new quiz
        st.session_state.data_df = load_data_from_gsheets()
        st.session_state.attempted_counts = st.session_state.data_df['Attempted'].to_dict()
        st.session_state.incorrect_attempt_counts = st.session_state.data_df['Incorrect attempt'].to_dict()

        # Reset other quiz state variables
        st.session_state.quiz_stage = 'chapter_selection'
        st.session_state.selected_chapters = []
        st.session_state.practice_mode = None
        st.session_state.num_questions_to_ask = 0
        st.session_state.quiz_questions = pd.DataFrame()
        st.session_state.current_question_idx = 0
        st.session_state.correct_answers_count = 0
        st.session_state.total_questions_asked = 0
        st.rerun() # Force rerun to go back to chapter selection