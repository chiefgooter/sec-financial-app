import streamlit as st
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai.errors import APIError
import time

# --- Configuration ---
GEMINI_MODEL = "gemini-2.5-flash"
st.set_page_config(
    page_title="Integrated Financial Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items=None
)

# --- Initialize Gemini Client ---
# The API Key is assumed to be set as an environment variable or via Streamlit secrets
try:
    if "GEMINI_API_KEY" in st.secrets:
        client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        # Fallback or development setup (will likely fail on deployment if key is missing)
        st.error("Gemini API Key not found in Streamlit secrets.")
        client = None 
except Exception as e:
    st.error(f"Error initializing Gemini client: {e}")
    client = None

# --- Custom Styling (Streamlit's equivalent of Tailwind/CSS) ---
st.markdown("""
<style>
.stApp {
    background-color: #0d1117; /* Dark background */
    color: #c9d1d9; /* Light text */
}
.stSidebar {
    background-color: #161b22; /* Slightly darker sidebar */
}
.stButton>button {
    background-color: #238636; /* GitHub green theme */
    color: white;
    font-weight: bold;
    border-radius: 8px;
    border: 1px solid #30363d;
    transition: all 0.2s;
}
.stButton>button:hover {
    background-color: #2ea043;
    border-color: #8b949e;
}
.reportview-container .main .block-container{
    padding-top: 2rem;
    padding-bottom: 2rem;
}
h1, h2, h3 {
    color: #58a6ff; /* Blue accents */
}
</style>
""", unsafe_allow_html=True)


# --- Core Search Function (Updated logic) ---

@st.cache_data(show_spinner="Searching for recent SEC Filings...")
def search_filings(ticker):
    """
    Uses the Gemini API with Google Search Grounding to find and list recent SEC filings.
    The function now only returns the formatted list of filings.
    """
    if not client:
        return "Error: Gemini client is not initialized.", []

    # MODIFIED SYSTEM PROMPT: Now only instructs the AI to return the numbered list.
    system_prompt = (
        "Act as an expert financial researcher. List the filing type, the filing date, and a direct URL link for the most recent "
        "10-K, 10-Q, and 8-K SEC filings you can find, up to a maximum of 10 total. "
        "Format the list as a single, numbered Markdown list where each item is a hyperlink using the filing name and date as the display text. "
        "Example list item format: 1. [10-Q filed 2024-10-25](http://example.com/url). "
        "Do NOT include any introductory or concluding remarks, or the link to the full EDGAR history."
    )
    user_query = f"Provide a list of recent SEC filings (10-K, 10-Q, 8-K) for {ticker}."

    # Exponential Backoff Implementation
    retries = 3
    delay = 2  # seconds
    
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_query,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[{"google_search": {}}]
                )
            )

            # Extract generated text
            generated_text = response.text

            # --- ROBUST SOURCE EXTRACTION LOGIC (using getattr) ---
            sources = []
            candidate = response.candidates[0] if response.candidates else None
            
            if candidate:
                grounding_metadata = getattr(candidate, 'grounding_metadata', None)
                
                if grounding_metadata:
                    # Safely retrieve the attributions list
                    attributions = getattr(grounding_metadata, 'grounding_attributions', [])
                    
                    for attribution in attributions:
                        # Safely retrieve the 'web' object
                        web_data = getattr(attribution, 'web', None)
                        
                        if web_data:
                            # Safely retrieve uri and title
                            uri = getattr(web_data, 'uri', None)
                            title = getattr(web_data, 'title', 'External Source')

                            if uri:
                                sources.append({
                                    'uri': uri,
                                    'title': title
                                })
            # --- END ROBUST SOURCE EXTRACTION LOGIC ---
            
            return generated_text, sources

        except APIError as e:
            if attempt < retries - 1:
                st.warning(f"API Error (Attempt {attempt + 1}): {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                st.error(f"API Error: Failed after {retries} attempts. {e}")
                return "Search failed due to an API connection error.", []
        except Exception as e:
            # This catch-all ensures we display the specific error, even if it's new
            st.error(f"An unexpected error occurred: {e}")
            return "Search failed due to an unexpected error.", []
            
    return "Search failed to complete.", []


# --- Streamlit App Layout ---

def main_app():
    st.title("Integrated Financial Dashboard")
    st.markdown("---")

    # --- Sidebar Input Section ---
    st.sidebar.markdown("### SEC Filing Search")
    
    ticker_input = st.sidebar.text_input(
        "Enter Ticker Symbol (e.g., MSFT, AAPL)",
        "MSFT",
        max_chars=5,
        key="sidebar_ticker_input"
    ).upper()
    
    # Check if the session state for the selected tab exists
    if 'selected_tab' not in st.session_state:
        st.session_state['selected_tab'] = "SEC Filings Analyzer"
        
    # Button text changed to "Search Filings"
    if st.sidebar.button("Search Filings", key="sidebar_analyze_button"):
        if ticker_input:
            st.session_state['analysis_ticker'] = ticker_input
            st.session_state['run_search'] = True
            # Set the selected tab to the Analyzer when the button is pressed
            st.session_state['selected_tab'] = "SEC Filings Analyzer"
        else:
            st.sidebar.warning("Please enter a ticker symbol.")

    # --- Sidebar Navigation (Below Input) ---
    st.sidebar.title("Navigation")
    
    # We use the session state to control the initial value of the radio button
    selected_tab = st.sidebar.radio(
        "Go to",
        ("SEC Filings Analyzer", "Dashboard"),
        index=0 if st.session_state['selected_tab'] == "SEC Filings Analyzer" else 1,
        key="navigation_radio"
    )
    # Update session state if the user changes the radio button
    st.session_state['selected_tab'] = selected_tab


    if st.session_state['selected_tab'] == "Dashboard":
        st.header("Welcome to Your Dashboard")
        st.info("Select 'SEC Filings Analyzer' or use the search box above to begin using the AI-powered tools.")
        st.markdown("### User Information")
        st.code("Current App State: Python Streamlit Application")

    elif st.session_state['selected_tab'] == "SEC Filings Analyzer":
        st.header("SEC Filings Search Results")
        st.markdown("Use AI to quickly find recent 10-K, 10-Q, and 8-K filings for the specified ticker.")

        # --- Search Execution and Display ---
        if 'run_search' in st.session_state and st.session_state['run_search']:
            st.markdown("---")
            # Ensure we have a ticker to analyze, default to MSFT if none has been searched yet
            ticker_to_search = st.session_state.get('analysis_ticker', 'MSFT')
            st.subheader(f"Recent Filings for: {ticker_to_search}")

            # NEW: Display the "View all filings" link first, using Streamlit code, not AI output
            sec_edgar_url = f"https://www.sec.gov/edgar/browse/?CIK={ticker_to_search}"
            st.markdown(f"[View all SEC Filings for {ticker_to_search} on EDGAR]({sec_edgar_url})")

            # Run the search function
            search_results_markdown, sources = search_filings(ticker_to_search)
            
            # Display Search Results
            st.header("Filings List")
            st.markdown(search_results_markdown) # This will ONLY render the numbered list of filings

            # Display Sources (Citations)
            st.markdown("### Grounding Sources (Sources used to generate the list)")
            if sources:
                for source in sources:
                    st.markdown(f"• [{source['title']}]({source['uri']})")
            else:
                st.info("No external sources were specifically cited for this response.")
            
            # Reset flag
            st.session_state['run_search'] = False


if __name__ == "__main__":
    main_app()
