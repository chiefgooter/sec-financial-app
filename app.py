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

# --- Define Major Companies for Global View ---
# We must aggregate data from specific companies since there is no single "All Filings" public API endpoint.
MAJOR_TICKERS = ["MSFT", "AAPL", "GOOGL", "AMZN", "NVDA", "TSLA", "JPM", "V", "JNJ", "WMT"]

# --- Initialize Gemini Client ---
try:
    if "GEMINI_API_KEY" in st.secrets:
        client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        st.error("Gemini API Key not found in Streamlit secrets. Please check .streamlit/secrets.toml.")
        client = None 
except Exception as e:
    st.error(f"Error initializing Gemini client: {e}")
    client = None

# --- Custom Styling (Streamlit's equivalent of Tailwind/CSS) ---
st.markdown("""
<style>
.stApp {
    background-color: #0d1117;
    color: #c9d1d9;
}
.stSidebar {
    background-color: #161b22;
}
.stButton>button {
    background-color: #238636;
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
    color: #58a6ff;
}
.stCode {
    background-color: #161b22;
}
</style>
""", unsafe_allow_html=True)


# --- Core Search Function (Direct SEC EDGAR API) ---

@st.cache_data(show_spinner=False, ttl=3600) # Cache for 1 hour to reduce SEC load
def fetch_sec_filings(ticker, limit=20, max_retries=3, all_filings=False): 
    """
    Fetches CIK and recent filings for a single ticker. Reduced limit and retries 
    to manage rate limits, especially when running multiple tickers.
    """
    headers = {'User-Agent': 'FinancialDashboardApp / myname@example.com'} 
    
    # 1. Get CIK
    cik_number = None
    try:
        cik_lookup_url = f"https://www.sec.gov/files/company_tickers.json"
        cik_response = requests.get(cik_lookup_url, headers=headers)
        cik_response.raise_for_status()
        cik_map = cik_response.json()
        
        for item in cik_map.values():
            if item['ticker'] == ticker.upper():
                cik_number = str(item['cik_str']).zfill(10)
                break
        
        if not cik_number:
            return [], f"SEC API Error: Could not find CIK for ticker {ticker}."

    except Exception as e:
        return [], f"An error occurred during CIK lookup for {ticker}: {e}"

    # 2. Get Filings using the CIK (Retry Loop)
    final_error = None
    for attempt in range(max_retries):
        try:
            wait_time = 3.0 + 3 * attempt # Shorter wait time since we are fetching less data per call
            time.sleep(wait_time) 
            
            filings_url = f"https://data.sec.gov/submissions/CIK{cik_number}.json"
            filings_response = requests.get(filings_url, headers=headers)
            filings_response.raise_for_status()
            data = filings_response.json()
            
            company_name = data.get('name', ticker) # Get the full company name
            recent_filings = []
            filings = data.get('filings', {}).get('recent', {})
            
            if not filings:
                final_error = f"SEC API Error: Filing structure missing for {ticker}."
                continue 

            filing_dates = filings.get('filingDate', [])
            filing_types = filings.get('type', [])
            accession_numbers = filings.get('accessionNumber', [])
            
            if not filing_types:
                final_error = f"SEC API Error: The list of filing types was missing for {ticker}. Retrying..."
                continue 

            num_filings = min(len(filing_dates), len(filing_types), len(accession_numbers))
            
            for i in range(num_filings):
                filing_type = filing_types[i]
                
                # Filter to common types OR if all_filings is requested, AND respect limit
                is_report_type = filing_type in ['10-K', '10-Q', '8-K', 'S-3', 'S-1']
                should_add_filing = (all_filings or is_report_type) and len(recent_filings) < limit
                
                if should_add_filing:
                    accession_number = accession_numbers[i]
                    filing_date = filing_dates[i]
                    accession_no_cleansed = accession_number.replace('-', '')
                    
                    document_url = (
                        f"https://www.sec.gov/Archives/edgar/data/{data['cik']}/"
                        f"{accession_no_cleansed}/{accession_number}-index.html"
                    )

                    recent_filings.append({
                        'Company': company_name, # Added Company Name for the aggregated view
                        'Ticker': ticker, # Added Ticker for the aggregated view
                        'Type': filing_type,
                        'Date': filing_date,
                        'Filing Name': f"{filing_type} filed on {filing_date}",
                        'Accession No.': accession_number,
                        'URL': document_url
                    })
            
            if recent_filings:
                return recent_filings, None
            else:
                final_error = f"No relevant filings found for {ticker}."
                return [], final_error
    
        except requests.exceptions.RequestException as e:
            final_error = f"Network Error for {ticker}: {e}"
        except json.JSONDecodeError:
            final_error = f"JSON Decode Error for {ticker}."
        except Exception as e:
            final_error = f"Unexpected error for {ticker}: {type(e).__name__} - {e}"

    return [], final_error

# --- NEW Aggregation Function ---
def fetch_all_major_filings(tickers):
    """Fetches and aggregates recent major filings from a list of tickers."""
    all_filings_data = []
    failed_tickers = []
    
    # Fetch a smaller limit per company (e.g., top 15) to keep the total list manageable and fast
    with st.empty():
        st.info(f"Loading recent filings for {len(tickers)} major companies. This may take a moment due to SEC rate limits.")
        progress_bar = st.progress(0)
    
        for i, ticker in enumerate(tickers):
            progress_bar.progress((i + 1) / len(tickers), text=f"Fetching filings for {ticker}...")
            
            # Fetch only major reports (10-K, 10-Q, 8-K etc.) for a cleaner aggregated view
            filings, error = fetch_sec_filings(ticker, limit=15, all_filings=False)
            
            if filings:
                all_filings_data.extend(filings)
            elif error and "Error during CIK lookup" not in error and "No relevant filings found" not in error:
                 # Log only severe errors, ignoring "No filings found" which is common
                failed_tickers.append(f"{ticker}: {error}")

        progress_bar.empty()

    if failed_tickers:
        st.warning(f"Failed to fetch data for some companies (e.g., {', '.join(t.split(':')[0] for t in failed_tickers[:3])}). Check the console for details.")
    
    return all_filings_data

# --- Scraping and Analysis Functions (Unchanged) ---
def scrape_filing_content(filing_url):
    """Fetches and cleans the text content from the main filing document."""
    try:
        headers = {'User-Agent': 'FinancialDashboardApp / myname@example.com'} 
        index_response = requests.get(filing_url, headers=headers)
        index_response.raise_for_status()
        index_soup = BeautifulSoup(index_response.content, 'html.parser')
        main_doc_link = index_soup.find('a', href=lambda href: href and (href.endswith('.htm') or href.endswith('.html')) and 'index' not in href.lower())
        
        if not main_doc_link:
            return None, "Error: Could not find the main HTML document link within the filing index."

        main_doc_path = main_doc_link['href']
        base_url = filing_url.rsplit('/', 1)[0] + '/'
        main_doc_url = base_url + main_doc_path

        doc_response = requests.get(main_doc_url, headers=headers)
        doc_response.raise_for_status()
        doc_soup = BeautifulSoup(doc_response.content, 'html.parser')
        
        for script_or_style in doc_soup(["script", "style"]):
            script_or_style.decompose()
            
        text = doc_soup.get_text()
        clean_text = ' '.join(text.split())
        
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
    st.title("Integrated Financial Dashboard")
    st.markdown("---")

    # --- Session State Initialization ---
    if 'selected_tab' not in st.session_state:
        st.session_state['selected_tab'] = "SEC Filings Analyzer"
    if 'global_filings_data' not in st.session_state:
        st.session_state['global_filings_data'] = []
    
    # --- Sidebar Input Section ---
    
    # 1. Ticker Input for Targeted Analyzer
    st.sidebar.markdown("### Targeted Ticker Analysis")
    ticker_input = st.sidebar.text_input(
        "Enter Ticker (e.g., MSFT)",
        "MSFT",
        max_chars=5,
        key="sidebar_analyzer_ticker_input"
    ).upper()
    
    if st.sidebar.button("Search & Analyze Ticker", key="sidebar_analyze_button"):
        if ticker_input:
            st.session_state['analysis_ticker'] = ticker_input
            st.session_state['run_search'] = True
            st.session_state['selected_tab'] = "SEC Filings Analyzer"
            st.cache_data.clear() 
        else:
            st.sidebar.warning("Please enter a ticker symbol.")

    st.sidebar.markdown("---")
    
    # 2. Global Filings Trigger (NEW)
    st.sidebar.markdown("### Global Filings Browser")
    st.sidebar.info(f"Fetches recent filings for {len(MAJOR_TICKERS)} major companies.")
    
    if st.sidebar.button("Load Global Filings", key="sidebar_load_global_button"):
        st.session_state['run_global_filings_search'] = True
        st.session_state['selected_tab'] = "Global Filings Browser"
        st.session_state['global_filings_data'] = [] # Clear previous data

    st.sidebar.markdown("---")

    # --- Sidebar Navigation (Below Input) ---
    st.sidebar.title("Navigation")
    
    selected_tab = st.sidebar.radio(
        "Go to",
        ("SEC Filings Analyzer", "Global Filings Browser", "Dashboard"),
        index=("SEC Filings Analyzer", "Global Filings Browser", "Dashboard").index(st.session_state['selected_tab']),
        key="navigation_radio"
    )
    st.session_state['selected_tab'] = selected_tab

    
    # --- Main Content: Dashboard ---
    if st.session_state['selected_tab'] == "Dashboard":
        st.header("Welcome to Your Dashboard")
        st.info("Use the sidebar tools to analyze specific company filings or browse major market reports.")

    # --- Main Content: Global Filings Browser Tab (NEW) ---
    elif st.session_state['selected_tab'] == "Global Filings Browser":
        st.header("Global Filings Browser (Top Market Reports)")
        st.markdown(f"**This view aggregates recent 10-K, 10-Q, and 8-K reports from {len(MAJOR_TICKERS)} major tickers.**")

        if 'run_global_filings_search' in st.session_state and st.session_state['run_global_filings_search']:
            # Fetch data if triggered
            filings_list = fetch_all_major_filings(MAJOR_TICKERS)
            
            if filings_list:
                df_global = pd.DataFrame(filings_list)
                # Sort by Date descending
                df_global['Date'] = pd.to_datetime(df_global['Date'])
                df_global = df_global.sort_values(by='Date', ascending=False)
                df_global['Date'] = df_global['Date'].dt.strftime('%Y-%m-%d') # Format back for display
                
                st.session_state['global_filings_data'] = df_global.to_dict('records')
                
                st.subheader(f"Found {len(df_global)} recent major filings.")
                
            else:
                st.info("No global filings were retrieved, likely due to SEC rate limits.")

            st.session_state['run_global_filings_search'] = False
            # Rerun to display the result (must happen after the session state is updated)
            st.experimental_rerun()
        
        # Display the stored data if it exists (handles display after rerun)
        if st.session_state['global_filings_data']:
            df_display_global = pd.DataFrame(st.session_state['global_filings_data'])
            
            # Create a combined 'Filing Link' column for easy access in the analyzer
            df_display_global['Filing Link'] = (
                df_display_global['Company'] + ' (' + 
                df_display_global['Ticker'] + ') - ' + 
                df_display_global['Type'] + ' (' + 
                df_display_global['Date'] + ')'
            )
            
            # Select relevant columns for display
            df_table = df_display_global[['Company', 'Ticker', 'Type', 'Date', 'Filing Link']].copy()
            
            st.markdown("**Select a row to use the filing's URL in the Analyzer tab.**")
            
            # Make the table selectable
            selected_rows = st.dataframe(
                df_table.drop(columns=['Filing Link']), 
                height=600, 
                use_container_width=True,
                hide_index=True,
                column_order=("Company", "Ticker", "Type", "Date"),
                selection_mode="single-row",
                key="global_filings_dataframe"
            )
            
            # Logic to pass selected filing to the Analyzer
            selected_index = selected_rows.selection['rows'][0] if selected_rows.selection and selected_rows.selection['rows'] else None
            
            if selected_index is not None:
                selected_filing = df_display_global.iloc[selected_index]
                
                # Update Analyzer session state variables
                st.session_state['selected_filing_url'] = selected_filing['URL']
                st.session_state['selected_filing_name'] = selected_filing['Filing Link']
                
                st.success(f"Selected filing: {selected_filing['Filing Link']}. Switch to the **SEC Filings Analyzer** tab to analyze it.")
        
    # --- Main Content: Analyzer Tab (Modified to accept global selection) ---
    elif st.session_state['selected_tab'] == "SEC Filings Analyzer":
        st.header("SEC Filings Analyzer (AI Powered)")
        st.markdown("Use this tab to search for a specific ticker and analyze its major filings.")
        
        # --- Handle direct search results ---
        if 'run_search' in st.session_state and st.session_state['run_search']:
            st.markdown("---")
            ticker_to_search = st.session_state.get('analysis_ticker', 'MSFT')
            st.subheader(f"Targeted Filings (10-K, 10-Q, 8-K) for: {ticker_to_search}")

            with st.spinner("Fetching targeted SEC Filings data (with retry logic)..."):
                filings_list, error_message = fetch_sec_filings(ticker_to_search, limit=100)
            
            if error_message:
                st.error(error_message)
                st.session_state['run_search'] = False
                return

            if filings_list:
                df = pd.DataFrame(filings_list)
                st.session_state['filings_df'] = df 
                
                # Display only relevant columns for selection
                df_display = df.drop(columns=['URL', 'Accession No.', 'Company', 'Ticker'])
                
                st.markdown("**Click a row below to select a filing.**")
                
                selected_rows = st.dataframe(
                    df_display, 
                    height=400, 
                    use_container_width=True,
                    hide_index=True,
                    column_order=("Type", "Date", "Filing Name"),
                    selection_mode="single-row",
                    key="analyzer_filings_dataframe"
                )

                selected_index = selected_rows.selection['rows'][0] if selected_rows.selection and selected_rows.selection['rows'] else None
                
                # Update selected filing details if a row is clicked
                if selected_index is not None:
                    selected_filing = df.iloc[selected_index]
                    st.session_state['selected_filing_url'] = selected_filing['URL']
                    st.session_state['selected_filing_name'] = selected_filing['Filing Name']

            else:
                st.info(f"No recent 10-K, 10-Q, or 8-K filings found for {ticker_to_search}.")
            
            st.session_state['run_search'] = False
            st.experimental_rerun()


        # --- Analysis Section (for both targeted and global selections) ---
        
        # Only display the analysis section if a filing URL is available from EITHER tab
        if st.session_state.get('selected_filing_url'):
            st.markdown("---")
            st.subheader(f"Analyze: {st.session_state.get('selected_filing_name', 'No Filing Selected')}")
            
            st.markdown(
                f"**View Full Filing:** [Open Document Link]({st.session_state['selected_filing_url']})"
            )

            analysis_prompt = st.text_area(
                "**AI Analysis Prompt (Gemini API):**",
                value=st.session_state.get('analysis_prompt', "Summarize the key events and material impacts discussed in the 'Management's Discussion and Analysis' section."),
                height=100
            )
            
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
                
                st.experimental_rerun() 

            if 'analysis_result' in st.session_state and st.session_state['analysis_result']:
                st.markdown("### AI Analysis Result")
                st.markdown(st.session_state['analysis_result'])
        else:
             st.info("Use the sidebar to search a ticker or load the Global Filings Browser to select a filing for analysis.")


if __name__ == "__main__":
    main_app()
