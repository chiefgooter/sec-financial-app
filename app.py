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


# --- Core Analysis Function ---

@st.cache_data(show_spinner="Searching for SEC Filings and Analyzing Content...")
def analyze_filings(ticker):
    """
    Uses the Gemini API with Google Search Grounding to find and summarize SEC filings.
    Implements exponential backoff for resilience.
    """
    if not client:
        return "Error: Gemini client is not initialized.", []

    system_prompt = (
        "Act as an expert financial analyst. Find the most recent 10-K and 10-Q SEC filings for the specified company. "
        "Summarize the key risks and opportunities from the 'Management's Discussion and Analysis' section of each filing "
        "into concise, detailed bullet points. Include at least two key points for both risks and opportunities from each "
        "filing type (10-K and 10-Q). Only return the summarized analysis text. Do NOT include a list of citation URIs, "
        "as Streamlit will display them separately."
    )
    user_query = f"Find the latest 10-K and 10-Q filings for the company with ticker {ticker} and summarize the key MD&A points."

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

            # Extract grounding sources (citations)
            sources = []
            if response.candidates and response.candidates[0].grounding_metadata:
                for attribution in response.candidates[0].grounding_metadata.grounding_attributions:
                    if attribution.web and attribution.web.uri:
                        sources.append({
                            'uri': attribution.web.uri,
                            'title': attribution.web.title or 'External Source'
                        })
            
            return generated_text, sources

        except APIError as e:
            if attempt < retries - 1:
                st.warning(f"API Error (Attempt {attempt + 1}): {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                st.error(f"API Error: Failed after {retries} attempts. {e}")
                return "Analysis failed due to an API connection error.", []
        except Exception as e:
            st.error(f"An unexpected error occurred: {e}")
            return "Analysis failed due to an unexpected error.", []
            
    return "Analysis failed to complete.", []


# --- Streamlit App Layout ---

def main_app():
    st.title("Integrated Financial Dashboard")
    st.markdown("---")

    # --- Sidebar Navigation ---
    st.sidebar.title("Navigation")
    
    # We will use simple radio buttons for navigation until we implement multi-page
    selected_tab = st.sidebar.radio(
        "Go to",
        ("SEC Filings Analyzer", "Dashboard"),
        index=0 # Start on the analyzer tab
    )

    if selected_tab == "Dashboard":
        st.header("Welcome to Your Dashboard")
        st.info("Select 'SEC Filings Analyzer' from the sidebar to begin using the AI-powered tools.")
        st.markdown("### User Information")
        # In a full Firebase integrated app, you'd show real user data here.
        st.code("Current App State: Python Streamlit Application")

    elif selected_tab == "SEC Filings Analyzer":
        st.header("SEC Filings Analyzer")
        st.markdown("Use AI to quickly summarize the key risks and opportunities from the latest 10-K and 10-Q filings.")

        # --- Input Section ---
        ticker = st.text_input(
            "Enter Ticker Symbol (e.g., MSFT, AAPL)",
            "MSFT",
            max_chars=5,
            key="ticker_input"
        ).upper()

        if st.button("Analyze Filings", key="analyze_button"):
            if ticker:
                st.session_state['analysis_ticker'] = ticker
                st.session_state['run_analysis'] = True
            else:
                st.warning("Please enter a ticker symbol.")

        # --- Analysis Execution and Display ---
        if 'run_analysis' in st.session_state and st.session_state['run_analysis']:
            st.markdown("---")
            st.subheader(f"Analysis for: {st.session_state['analysis_ticker']}")

            # Run the analysis function
            summary_text, sources = analyze_filings(st.session_state['analysis_ticker'])
            
            # Display Summary
            st.markdown("### AI Summary of MD&A")
            st.write(summary_text)

            # Display Sources (Citations)
            st.markdown("### Grounding Sources")
            if sources:
                for source in sources:
                    st.markdown(f"• [{source['title']}]({source['uri']})")
            else:
                st.info("No external sources were specifically cited for this response.")
            
            # Reset flag
            st.session_state['run_analysis'] = False


if __name__ == "__main__":
    main_app()
