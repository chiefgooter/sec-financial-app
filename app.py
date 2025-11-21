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
    # Use st.secrets to access the API key from the .streamlit/secrets.toml file
    if "GEMINI_API_KEY" in st.secrets:
        client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        # Fallback or development setup (will likely fail on deployment if key is missing)
        st.error("Gemini API Key not found in Streamlit secrets. Please check .streamlit/secrets.toml.")
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
.stCode {
    background-color: #161b22;
}
</style>
""", unsafe_allow_html=True)


# --- Core Search Function (Direct SEC EDGAR API) ---

# This function remains unchanged, as the issue is external (SEC rate limit)
def fetch_sec_filings(ticker, limit=100, max_retries=5): 
    """
    Fetches the CIK and then the last 100 recent filings (10-K, 10-Q, 8-K, S-1, S-3) 
    directly from the SEC's EDGAR API, including robust retry logic for malformed data.
    """
    # SEC requires a user-agent header
    headers = {'User-Agent': 'FinancialDashboardApp / myname@example.com'} 
    
    # --- 1. Get CIK (Central Index Key) for the Ticker (Always outside retry loop) ---
    cik_number = None
    try:
        cik_lookup_url = f"https://www.sec.gov/files/company_tickers.json"
        cik_response = requests.get(cik_lookup_url, headers=headers)
        cik_response.raise_for_status()
        cik_map = cik_response.json()
        
        for item in cik_map.values():
            if item['ticker'] == ticker.upper():
                cik_number = str(item['cik_str']).zfill(10) # Pad CIK to 10 digits
                break
        
        if not cik_number:
            return [], f"SEC API Error: Could not find CIK for ticker {ticker}. Please verify the ticker symbol."

    except Exception as e:
        return [], f"An error occurred during CIK lookup: {e}"

    # --- 2. Get Filings using the CIK (Retry Loop) ---
    final_error = None
    for attempt in range(max_retries):
        try:
            # Increased delay significantly to respect aggressive SEC rate limiting.
            wait_time = 5.0 + 5 * attempt 
            st.toast(f"Attempt {attempt + 1}/{max_retries}: Waiting {wait_time:.1f}s before fetching filings.", icon="⏳")
            time.sleep(wait_time) 
            
            filings_url = f"https://data.sec.gov/submissions/CIK{cik_number}.json"
            
            filings_response = requests.get(filings_url, headers=headers)
            filings_response.raise_for_status()
            data = filings_response.json()
            
            recent_filings = []
            
            # Robustly access the 'recent' filings dictionary
            filings = data.get('filings', {}).get('recent', {})
            
            if not filings:
                final_error = (f"SEC API Error: Filing structure missing in response for {ticker}. "
                            f"This may indicate a temporary SEC issue or rate limit blockage.")
                continue # Retry if data is completely missing

            # Robustly extract lists for required columns
            filing_dates = filings.get('filingDate', [])
            filing_types = filings.get('type', [])
            accession_numbers = filings.get('accessionNumber', [])

            # CRITICAL CHECK: If filing_types is missing (the root cause of the previous error), retry.
            if not filing_types:
                current_error = (f"SEC API Error: The list of filing types was missing or empty. "
                                f"Filings lengths found: Dates={len(filing_dates)}, Types=0, Accession={len(accession_numbers)}. Retrying...")
                
                if attempt < max_retries - 1:
                    final_error = current_error 
                    continue # Explicitly retry
                else:
                    final_error = current_error
                    break # Stop retrying

            # The length of the lists should be the same. Use the shortest length for safety.
            num_filings = min(len(filing_dates), len(filing_types), len(accession_numbers))
            
            if num_filings == 0:
                current_error = (f"SEC API Error: Found company data for {ticker}, but zero filings were processed. "
                                f"Filings lengths found: Dates={len(filing_dates)}, Types={len(filing_types)}, Accession={len(accession_numbers)}.")
                
                if attempt < max_retries - 1:
                    final_error = current_error
                    continue # Retry
                else:
                    final_error = current_error
                    break # Stop retrying
            
            # --- Success: Parse Filings ---
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
                return [], f"Found data for {ticker}, but no 10-K, 10-Q, or 8-K filings were in the top {limit} results."
            
            return recent_filings, None
    
        except requests.exceptions.HTTPError as e:
            return [], f"SEC API HTTP Error: {e}. The SEC may be blocking the request or the ticker may be invalid."
        
        except json.JSONDecodeError as e:
            current_error = f"SEC API Error: Could not decode JSON response. Status: {filings_response.status_code if 'filings_response' in locals() else 'N/A'}."
            if attempt < max_retries - 1:
                final_error = current_error
                continue
            else:
                final_error = current_error
                break 
        
        except Exception as e:
            current_error = f"An unexpected error occurred during SEC data fetching: {type(e).__name__} - {e}"
            if attempt < max_retries - 1:
                final_error = current_error
                continue
            else:
                final_error = current_error
                break 
    
    return [], final_error


# --- NEW: Scraping and Analysis Functions ---

def scrape_filing_content(filing_url):
    """Fetches and cleans the text content from the main filing document."""
    try:
        # SEC requires a user-agent header for all requests
        headers = {'User-Agent': 'FinancialDashboardApp / myname@example.com'} 
        
        # We need to find the link to the primary HTML document within the index page
        index_response = requests.get(filing_url, headers=headers)
        index_response.raise_for_status()
        
        index_soup = BeautifulSoup(index_response.content, 'html.parser')
        
        # Common pattern: find the main document link (usually PDF or HTM)
        # We look for the first link with an HMT or HTML extension that isn't the index itself
        main_doc_link = index_soup.find('a', href=lambda href: href and (href.endswith('.htm') or href.endswith('.html')) and 'index' not in href.lower())
        
        if not main_doc_link:
            return None, "Error: Could not find the main HTML document link within the filing index."

        main_doc_path = main_doc_link['href']
        
        # Construct the full URL for the main document
        base_url = filing_url.rsplit('/', 1)[0] + '/'
        main_doc_url = base_url + main_doc_path

        # Fetch the main document
        doc_response = requests.get(main_doc_url, headers=headers)
        doc_response.raise_for_status()
        
        doc_soup = BeautifulSoup(doc_response.content, 'html.parser')
        
        # Extract text, removing script and style tags
        for script_or_style in doc_soup(["script", "style"]):
            script_or_style.decompose()
            
        # Get all text and clean up whitespace
        text = doc_soup.get_text()
        clean_text = ' '.join(text.split())
        
        # Truncate text to fit within the Gemini API context window (roughly 128,000 tokens)
        # We will truncate to 500,000 characters to be safe.
        MAX_CHARS = 500000 
        if len(clean_text) > MAX_CHARS:
            st.warning(f"Filing content was truncated from {len(clean_text):,} to {MAX_CHARS:,} characters to fit the API context window.")
            clean_text = clean_text[:MAX_CHARS]
        
        return clean_text, None

    except requests.exceptions.RequestException as e:
        return None, f"Network/HTTP Error during scraping: {e}"
    except Exception as e:
        return None, f"An unexpected error occurred during scraping: {e}"


def analyze_filing_content(content, analysis_prompt):
    """Calls the Gemini API to analyze the scraped content."""
    if not client:
        return "Gemini client is not initialized due to missing API key."
    
    system_instruction = (
        "You are a world-class financial analyst specializing in SEC filings. "
        "Your task is to analyze the provided SEC filing text based on the user's prompt. "
        "Provide a concise, professional, and accurate summary or analysis. "
        "Only use the information provided in the SEC text."
    )
    
    user_query = f"Based on the following SEC document text, provide the requested analysis:\n\n-- DOCUMENT TEXT --\n{content}\n\n-- ANALYSIS REQUEST --\n{analysis_prompt}"
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_query,
            config=dict(
                system_instruction=system_instruction
            )
        )
        return response.text
    except APIError as e:
        return f"Gemini API Error: {e}. Check your API key and usage limits."
    except Exception as e:
        return f"An unexpected error occurred during AI analysis: {e}"


# --- Streamlit App Layout ---

def main_app():
    # ... (rest of main_app setup remains the same) ...
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
            st.cache_data.clear() 
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

            # 1. Fetch Filings (Now without caching and inside a spinner)
            with st.spinner("Fetching structured SEC Filings data (with retry logic)..."):
                filings_list, error_message = fetch_sec_filings(ticker_to_search, limit=100)
            
            # 2. Handle Errors
            if error_message:
                st.error(error_message)
                st.session_state['run_search'] = False
                st.session_state.pop('filings_df', None) # Clear previous data
                return

            # 3. Display Filings in a Scrollable, Selectable Dataframe
            if filings_list:
                df = pd.DataFrame(filings_list)
                st.session_state['filings_df'] = df # Store for later use
                
                df_display = df.drop(columns=['URL', 'Accession No.'])
                
                st.markdown("**Click a row below to select a filing.**")
                
                selected_rows = st.dataframe(
                    df_display, 
                    height=400, 
                    use_container_width=True,
                    hide_index=True,
                    column_order=("Type", "Date", "Filing Name"),
                    selection_mode="single-row",
                    key="filings_dataframe"
                )

                # --- NEW: Analysis Logic Trigger ---
                
                # Check for selected row (uses st.dataframe key)
                selected_index = selected_rows.selection['rows'][0] if selected_rows.selection and selected_rows.selection['rows'] else None

                if selected_index is not None:
                    selected_filing = df.iloc[selected_index]
                    st.session_state['selected_filing_url'] = selected_filing['URL']
                    st.session_state['selected_filing_name'] = selected_filing['Filing Name']

                # Display analysis section if a filing is selected OR if a previous selection exists
                if st.session_state.get('selected_filing_url'):
                    st.markdown("---")
                    st.subheader(f"Analyze: {st.session_state['selected_filing_name']}")
                    
                    st.markdown(
                        f"**View Full Filing:** [{selected_filing['Accession No.'] if selected_index is not None else 'Link'}]({st.session_state['selected_filing_url']})"
                    )

                    analysis_prompt = st.text_area(
                        "**AI Analysis Prompt (Gemini API):**",
                        value="Summarize the key events and material impacts discussed in the 'Management's Discussion and Analysis' section.",
                        height=100
                    )
                    
                    # Store prompt in session state for rerun persistence
                    st.session_state['analysis_prompt'] = analysis_prompt 

                    if st.button("Run AI Analysis", key="run_ai_analysis"):
                        st.session_state['analysis_result'] = ""
                        
                        with st.spinner(f"1/2: Scraping content from {st.session_state['selected_filing_name']}..."):
                            filing_content, scrape_error = scrape_filing_content(st.session_state['selected_filing_url'])
                        
                        if scrape_error:
                            st.error(scrape_error)
                        elif filing_content:
                            with st.spinner(f"2/2: Sending content to Gemini for analysis..."):
                                analysis_text = analyze_filing_content(filing_content, analysis_prompt)
                                st.session_state['analysis_result'] = analysis_text
                        
                        # Rerun to display result cleanly
                        st.experimental_rerun() 

                    # Display Analysis Result
                    if 'analysis_result' in st.session_state and st.session_state['analysis_result']:
                        st.markdown("### AI Analysis Result")
                        st.markdown(st.session_state['analysis_result'])
                
            else:
                st.info(f"No recent 10-K, 10-Q, or 8-K filings found for {ticker_to_search} in the top 100 results.")
            
            # Reset flag
            st.session_state['run_search'] = False


if __name__ == "__main__":
    main_app()
