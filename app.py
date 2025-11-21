import streamlit as st
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai.errors import APIError
import time
import pandas as pd
import json

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


# --- Core Search Function (Direct SEC EDGAR API) ---

@st.cache_data(ttl=3600, show_spinner="Fetching structured SEC Filings data...")
def fetch_sec_filings(ticker, limit=100):
    """
    Fetches the CIK and then the last 100 recent filings (10-K, 10-Q, 8-K, S-1, S-3) 
    directly from the SEC's EDGAR API.
    """
    # SEC requires a user-agent header
    # NOTE: SEC has strict limits on requests. Use proper headers and caching.
    headers = {'User-Agent': 'FinancialDashboardApp / myname@example.com'} 
    
    try:
        # 1. Get CIK (Central Index Key) for the Ticker
        cik_lookup_url = f"https://www.sec.gov/files/company_tickers.json"
        
        cik_response = requests.get(cik_lookup_url, headers=headers)
        cik_response.raise_for_status()
        cik_map = cik_response.json()
        
        # Find the correct CIK
        cik_number = None
        for item in cik_map.values():
            if item['ticker'] == ticker.upper():
                cik_number = str(item['cik_str']).zfill(10) # Pad CIK to 10 digits
                break

        if not cik_number:
            return [], f"SEC API Error: Could not find CIK for ticker {ticker}. Please verify the ticker symbol."

        # 2. Get Filings using the CIK
        filings_url = f"https://data.sec.gov/submissions/CIK{cik_number}.json"
        
        filings_response = requests.get(filings_url, headers=headers)
        filings_response.raise_for_status()
        data = filings_response.json()
        
        recent_filings = []
        
        # Robustly access the 'recent' filings dictionary
        filings = data.get('filings', {}).get('recent', {})
        
        # --- START FIX: Enhanced Data Structure Check ---
        if not filings:
            # This is the most likely cause of the "unexpected data format" error if the CIK lookup passed.
            return [], (f"SEC API Error: Filing structure missing in response for {ticker}. "
                        f"This may indicate a temporary SEC issue or rate limit blockage.")

        # Robustly extract lists for required columns
        filing_dates = filings.get('filingDate', [])
        filing_types = filings.get('type', [])
        accession_numbers = filings.get('accessionNumber', [])

        # The length of the lists should be the same. Use the shortest length just in case.
        num_filings = min(len(filing_dates), len(filing_types), len(accession_numbers))
        
        if num_filings == 0:
            # If the lists exist but are empty (or were not found and defaulted to []).
             return [], (f"SEC API Error: Found company data for {ticker}, but zero recent filings "
                         f"were available or the data was malformed (num_filings=0).")
        # --- END FIX ---
        
        # 3. Iterate and Construct Filing Objects
        for i in range(num_filings):
            filing_type = filing_types[i]
            
            # Filter to common types and respect the limit
            if filing_type in ['10-K', '10-Q', '8-K', 'S-3', 'S-1'] and len(recent_filings) < limit:
                
                accession_number = accession_numbers[i]
                filing_date = filing_dates[i]
                
                # Construct the direct filing URL (link to the full HTML index)
                accession_no_cleansed = accession_number.replace('-', '')
                
                document_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{data['cik']}/"
                    f"{accession_no_cleansed}/{accession_number}-index.html"
                )

                recent_filings.append({
                    'Type': filing_type,
                    'Date': filing_date,
                    'Filing Name': f"{filing_type} filed on {filing_date}",
                    'Accession No.': accession_number,
                    'URL': document_url
                })
        
        if not recent_filings:
            # If we successfully parsed the data but the filtered list is empty
            return [], f"Found data for {ticker}, but no 10-K, 10-Q, 8-K, S-1, or S-3 filings were in the top {limit} results."


        return recent_filings, None
    
    except requests.exceptions.HTTPError as e:
        # Print to console for debugging
        print(f"DEBUG: SEC API HTTP Error for {ticker}: {e}") 
        return [], f"SEC API HTTP Error: {e}. The SEC may be blocking the request or the ticker may be invalid."
    except json.JSONDecodeError as e:
        # Print to console for debugging
        print(f"DEBUG: SEC JSON Decode Error for {ticker}: {e}. Response status: {filings_response.status_code if 'filings_response' in locals() else 'N/A'}")
        return [], f"SEC API Error: Could not decode JSON response. The SEC may have sent malformed data due to rate limiting or blocking."
    except Exception as e:
        # Print to console for debugging
        print(f"DEBUG: An unexpected error during SEC data fetching for {ticker}: {e}")
        return [], f"An unexpected error occurred during SEC data fetching: {e}"


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
    
    if 'selected_tab' not in st.session_state:
        st.session_state['selected_tab'] = "SEC Filings Analyzer"
        
    if st.sidebar.button("Search Filings", key="sidebar_analyze_button"):
        if ticker_input:
            st.session_state['analysis_ticker'] = ticker_input
            st.session_state['run_search'] = True
            st.session_state['selected_tab'] = "SEC Filings Analyzer"
        else:
            st.sidebar.warning("Please enter a ticker symbol.")

    # --- Sidebar Navigation (Below Input) ---
    st.sidebar.title("Navigation")
    
    selected_tab = st.sidebar.radio(
        "Go to",
        ("SEC Filings Analyzer", "Dashboard"),
        index=0 if st.session_state['selected_tab'] == "SEC Filings Analyzer" else 1,
        key="navigation_radio"
    )
    st.session_state['selected_tab'] = selected_tab


    if st.session_state['selected_tab'] == "Dashboard":
        st.header("Welcome to Your Dashboard")
        st.info("Select 'SEC Filings Analyzer' or use the search box above to begin using the AI-powered tools.")
        st.markdown("### User Information")
        st.code("Current App State: Python Streamlit Application")

    elif st.session_state['selected_tab'] == "SEC Filings Analyzer":
        st.header("SEC Filings Search Results")
        st.markdown("Select a filing from the list below to analyze or view.")

        # --- Search Execution and Display ---
        if 'run_search' in st.session_state and st.session_state['run_search']:
            st.markdown("---")
            ticker_to_search = st.session_state.get('analysis_ticker', 'MSFT')
            st.subheader(f"Recent Filings (up to 100) for: {ticker_to_search}")

            # 1. Fetch Filings
            filings_list, error_message = fetch_sec_filings(ticker_to_search, limit=100)
            
            # 2. Handle Errors
            if error_message:
                st.error(error_message)
                st.session_state['run_search'] = False
                # If the error is not about finding filings, log it.
                if "no 10-K, 10-Q, 8-K" not in error_message:
                    st.toast("Filing fetch error. Check console for details.", icon="⚠️")
                return

            # 3. Display Filings in a Scrollable, Selectable Dataframe
            if filings_list:
                df = pd.DataFrame(filings_list)
                
                # Drop the URL column for the display, but keep it for selection logic
                df_display = df.drop(columns=['URL', 'Accession No.'])
                
                st.markdown("**Click a row below to select a filing.**")
                
                # Use st.dataframe with selection enabled for easy scrolling and selection
                selected_rows = st.dataframe(
                    df_display, 
                    height=400, # Set height to make it scrollable
                    use_container_width=True,
                    hide_index=True,
                    column_order=("Type", "Date", "Filing Name"),
                    selection_mode="single-row"
                )

                # 4. Handle Selection
                if selected_rows.selection and selected_rows.selection['rows']:
                    selected_index = selected_rows.selection['rows'][0]
                    selected_filing = df.iloc[selected_index]
                    
                    st.markdown("---")
                    st.subheader(f"Selected Filing: {selected_filing['Filing Name']}")
                    
                    # Provide direct link to the filing
                    st.markdown(
                        f"**View Full Filing:** [{selected_filing['Accession No.']}]({selected_filing['URL']})"
                    )
                    
                    # Placeholder for AI Analysis based on selection
                    st.info(
                        "**Next Step:** You can now integrate the Gemini API here to analyze the content of this specific filing! "
                        "For example, you could ask the AI to summarize the 'Risk Factors' section."
                    )
            else:
                # This is the expected output if no matching filings were found, and is handled above.
                st.info(f"No recent 10-K, 10-Q, or 8-K filings found for {ticker_to_search} in the top 100 results.")
            
            # Reset flag
            st.session_state['run_search'] = False


if __name__ == "__main__":
    main_app()
