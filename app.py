import streamlit as st
import requests
import json
import pandas as pd
from time import sleep
from io import StringIO
import re
from datetime import date
from dateutil.relativedelta import relativedelta
from bs4 import BeautifulSoup

# --- Configuration & State ---
# Initialize the user-agent headers. These will be updated from the sidebar inputs.
HEADERS = {
    'User-Agent': 'DefaultAppName / default@example.com', # SEC compliance placeholder
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'www.sec.gov'
}

# Initialize session state for CIK storage
if 'target_cik' not in st.session_state:
    st.session_state.target_cik = "320193" # Default CIK for Apple
if 'master_filings_df' not in st.session_state:
    st.session_state.master_filings_df = pd.DataFrame()

# FIX: Corrected typo from st.session_session_state to st.session_state
if 'loaded_index_key' not in st.session_state: 
    st.session_state.loaded_index_key = ""

# --- CIK Lookup Function (Using Search API for Stability) ---
# NOTE: This function remains as-is but is now isolated in the "CIK Lookup" tab due to instability.

@st.cache_data(ttl=86400) # Cache CIK mapping for 24 hours to reduce load on SEC
def get_cik_data(ticker, headers):
    """
    Attempts to fetch the CIK and company name using the SEC's EDGAR full-text search.
    """
    SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    params = {
        'action': 'getcompany',
        'Company': ticker,
        'output': 'xml' # Requesting XML output for easier parsing (it's actually a form of HTML)
    }

    try:
        sleep(0.5) 
        response = requests.get(SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()

        found_cik = None
        
        # --- ROBUST CIK EXTRACTION (Attempt 1: CIK label) ---
        # Look for the CIK link directly (e.g., /edgar/data/0000320193/...)
        cik_match_1 = re.search(r'/edgar/data/(\d{10})/', response.text)
        
        if cik_match_1:
            found_cik = cik_match_1.group(1)
            
        # --- ROBUST CIK EXTRACTION (Attempt 2: CIK label with padded digits) ---
        if not found_cik:
             cik_match_2 = re.search(r'CIK:[^>]*?(\d{1,10})', response.text)
             if cik_match_2:
                found_cik = cik_match_2.group(1).zfill(10) # Pad to 10 digits
        
        if found_cik:
            # Simple way to get the company name from the response title
            name_match = re.search(r'<title>(.+?) - S', response.text)
            company_name = name_match.group(1) if name_match else f"Ticker Search: {ticker}"
            return found_cik, company_name
        
        return None, None
        
    except requests.exceptions.RequestException as e:
        st.warning("⚠️ Ticker Lookup Service Down ⚠️")
        st.error(f"Error: Could not use the SEC search API to find the CIK. Please enter the company's CIK manually.")
        st.caption(f"Details: {e}")
        return None, None

# --- Core Financial Facts Data Fetching Function (Primary data source) ---

@st.cache_data(ttl=3600) # Cache the facts data for 1 hour
def fetch_sec_company_facts(cik, headers):
    """
    Fetches the company facts JSON data from the SEC EDGAR API for a given CIK.
    """
    padded_cik = str(cik).zfill(10)
    FACTS_URL = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json'
    
    sleep(0.5) 
    
    try:
        # Ensure the Host header is correct for the data.sec.gov domain
        data_headers = headers.copy()
        data_headers['Host'] = 'data.sec.gov'
        
        response = requests.get(FACTS_URL, headers=data_headers)
        response.raise_for_status() 
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            st.error(f"Error: Could not find financial facts for CIK {cik}. Check if CIK is correct.")
        elif response.status_code == 403:
            st.error("Error 403 Forbidden: Check your User-Agent header (Application Name and Email) in the sidebar. The SEC may be blocking your request.")
        else:
            st.error(f"HTTP Error {response.status_code}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data: {e}. Check network connection or SEC API status.")
        return None

# --- NEW: Secondary Filing List Fetcher (More robust than facts API 'listFilings') ---

@st.cache_data(ttl=3600)
def fetch_edgar_filings_list(cik, headers):
    """
    Scrapes the company's EDGAR document list page for recent filings.
    This is used as a fallback/primary source for the filing list, as the facts API is unreliable for this list.
    """
    padded_cik = str(cik).zfill(10)
    # The URL to the company's main EDGAR page listing all documents
    EDGAR_URL = f'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded_cik}&type=&dateb=&owner=exclude&count=100'
    
    sleep(0.5) 
    
    try:
        data_headers = headers.copy()
        data_headers['Host'] = 'www.sec.gov' 
        
        response = requests.get(EDGAR_URL, headers=data_headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Look for the main table containing the filing list
        filings_table = soup.find('table', class_='tableFile2')
        if not filings_table:
            st.warning(f"Could not find the filing list table on the EDGAR page for CIK {cik}.")
            return []

        filings_data = []
        # Iterate over all rows, skipping the header row
        for row in filings_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 5: # Expect at least 5 columns: type, link to filing, description, date, file number
                form_type = cols[0].text.strip()
                date_filed = cols[3].text.strip()
                
                # Find the link to the document itself (which is often in the 2nd column)
                document_link_tag = cols[1].find('a')
                if document_link_tag:
                    # The href is relative, so we need to prepend the base URL
                    relative_href = document_link_tag.get('href')
                    document_link = f"https://www.sec.gov{relative_href}"
                    filings_data.append({
                        'Filing Type': form_type,
                        'Filing Date': date_filed,
                        'Link': document_link,
                        'Description': 'Link to Index File' # Simple description for this method
                    })
        
        return filings_data

    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching filings from EDGAR page for CIK {cik}: {e}")
        return []

# --- Function to fetch a recent Master Index File ---

@st.cache_data(ttl=86400) # Cache the massive index file for 24 hours
def fetch_master_index_filings(year, qtr, headers):
    """
    Fetches and parses a quarterly master index file containing thousands of filings.
    This provides a non-company-specific list of filings.
    """
    MASTER_INDEX_URL = f'https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/master.idx'
    
    st.info(f"Fetching SEC Master Index for {year} Q{qtr}. This file contains thousands of filings and may take a moment to load.")
    
    sleep(0.5)
    
    try:
        data_headers = headers.copy()
        data_headers['Host'] = 'www.sec.gov' 
        
        response = requests.get(MASTER_INDEX_URL, headers=data_headers)
        response.raise_for_status() 
        
        content = response.text
        
        # Check if the content is just the header (i.e., the file is empty or too short)
        if len(content.splitlines()) < 12:
            st.warning("The fetched index file appears to be empty or incomplete (only headers found). Try a different quarter.")
            return pd.DataFrame()

        # Skip the first 11 lines of header information (lines 0-10)
        content_lines = content.splitlines()
        data_lines = content_lines[11:] 
        
        data = StringIO("\n".join(data_lines))
        df = pd.read_csv(data, sep='|', header=None, 
                         names=['CIK', 'Company Name', 'Form Type', 'Date Filed', 'Filename'])
        
        def create_index_sec_link(row):
             return f"https://www.sec.gov/Archives/{row['Filename']}"

        df['Link'] = df.apply(create_index_sec_link, axis=1)

        return df
        
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching Master Index for {year} Q{qtr}: Failed to connect or received a bad response.")
        st.caption(f"Details: {e}")
        return pd.DataFrame()


# --- Data Analysis and Presentation Function ---

def display_key_metrics(data, identifier):
    """
    Analyzes and displays key US-GAAP metrics in a Streamlit interface.
    """
    company_name = data.get('entityName', 'N/A')
    st.subheader(f"Financial Summary for {company_name} ({identifier})")
    
    us_gaap = data['facts'].get('us-gaap', {})
    
    metrics_to_find = {
        "Revenues": "Latest Reported Revenue",
        "Assets": "Latest Reported Total Assets",
        "NetIncomeLoss": "Latest Reported Net Income / Loss"
    }

    cols = st.columns(len(metrics_to_find))
    
    for i, (gaap_tag, display_name) in enumerate(metrics_to_find.items()):
        
        metric_data = us_gaap.get(gaap_tag, {}).get('units', {}).get('USD', [])
        
        with cols[i]:
            if metric_data:
                metric_data.sort(key=lambda x: x.get('end', '0'), reverse=True)
                latest_metric = metric_data[0]
                
                value = latest_metric['val']
                end_date = latest_metric['end']
                
                st.metric(label=display_name, value=f"${value:,.0f}")
                st.caption(f"Period End: {end_date}")
            else:
                st.metric(label=display_name, value="N/A")

# --- Company Filings Presentation Function ---

def display_company_filings(data, cik, company_name, headers):
    """
    Displays the recent company filings. It first tries to use the facts API data,
    then falls back to scraping the EDGAR page for a more complete list.
    """
    st.markdown("---")
    st.header(f"Recent Filings ({company_name})")

    # 1. Try to get filing list from facts API (unreliable)
    filings_list_from_facts = data.get('listFilings', [])

    filings_for_df = []
    
    if filings_list_from_facts:
        st.caption("Using filing data from the structured 'facts' API.")
        for filing in filings_list_from_facts:
            # Construct a direct link to the full text document
            acc_no = filing.get('accessionNumber', 'N/A').replace('-', '')
            link = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/{filing.get('accessionNumber', 'N/A')}.txt"
            filings_for_df.append({
                'Filing Type': filing.get('form', 'N/A'),
                'Filing Date': filing.get('filingDate', 'N/A'),
                'Description': filing.get('formDescription', 'N/A'),
                'Link': link
            })
    else:
        # 2. Fallback: Scrape the company's main EDGAR page (more reliable list)
        st.warning("Structured filing list missing from the facts API. Falling back to EDGAR document search.")
        with st.spinner("Fetching list of recent filings from the main EDGAR search page..."):
            filings_for_df = fetch_edgar_filings_list(cik, headers)
        
        if not filings_for_df:
            st.warning("No recent filings data available from any source.")
            return

    df = pd.DataFrame(filings_for_df)
    
    # Identify all available filing types
    all_filing_types = set(df['Filing Type'].unique())
    
    # 1. Add Filing Type Filter
    default_selection = [t for t in ['10-K', '10-Q', '8-K', '4'] if t in all_filing_types]
    if not default_selection and all_filing_types:
        # Default to the first few if common ones aren't found
        default_selection = sorted(list(all_filing_types))[:3]
    
    selected_types = st.multiselect(
        "Filter Filings by Type:",
        options=sorted(list(all_filing_types)),
        default=default_selection
    )

    if not selected_types:
        st.warning("Select one or more filing types to display.")
        return
        
    df_filtered = df[df['Filing Type'].isin(selected_types)]

    # Limit to the top 20 recent filtered filings for a cleaner view
    df_display = df_filtered.head(20).copy()

    df_final = df_display[['Filing Type', 'Filing Date', 'Description', 'Link']]
    
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document")
        }
    )
    st.caption(f"Showing {len(df_final)} of the most recent filtered filings for {company_name}.")

# --- CALLBACK FUNCTION FOR TICKET SEARCH ---
def search_cik_callback(app_name, email):
    """
    Callback function executed when the 'Search CIK' button is pressed.
    """
    ticker = st.session_state.ticker_input.strip().upper()
    
    if not ticker:
        st.error("Please enter a Stock Ticker to search.")
        st.session_state.target_cik = ""
        return

    if app_name.strip() == "MySECApp" or email.strip() == "user@example.com":
         st.warning("Please update the Application Name and Contact Email in the sidebar for SEC compliance.")

    with st.spinner(f"Attempting ticker lookup for {ticker}..."):
        found_cik, company_name = get_cik_data(ticker, HEADERS)
        
        if found_cik:
            # Update the main CIK input and the target_cik state
            st.session_state.cik_input_final = found_cik 
            st.session_state.target_cik = found_cik
            st.success(f"CIK found: **{found_cik}** (Company: {company_name}). Switch to the 'Company Filings & Metrics' tab to use it.")
        else:
            st.session_state.target_cik = ""
            if not company_name:
                 st.error(f"Could not find a CIK for ticker: {ticker}. This feature is experimental due to SEC instability.")

# --- CALLBACK FUNCTION TO LOAD MASTER INDEX ---
def load_master_index_callback(selected_key, index_options):
    """
    Loads the master index data and stores it in session state.
    """
    year, qtr = index_options[selected_key]
    
    # Only reload if the key has changed
    if st.session_state.loaded_index_key != selected_key:
        
        master_df = fetch_master_index_filings(year, qtr, HEADERS)
        
        if not master_df.empty:
            st.session_state.master_filings_df = master_df
            st.session_state.loaded_index_key = selected_key
        else:
            st.session_state.master_filings_df = pd.DataFrame()
            st.session_state.loaded_index_key = ""
            
# --- CALLBACK FUNCTION TO CLEAR MASTER INDEX ---
def clear_master_index_callback():
    st.session_state.master_filings_df = pd.DataFrame()
    st.session_state.loaded_index_key = ""


# --- Streamlit Main App Layout ---

def main():
    st.set_page_config(
        page_title="SEC EDGAR Data Viewer",
        layout="wide", # Use wide layout for better display of large dataframes
        initial_sidebar_state="expanded"
    )
    
    st.title("SEC EDGAR Data Viewer")
    
    # --- Sidebar for SEC Compliance ---
    st.sidebar.header("SEC Compliance (Required)")
    st.sidebar.markdown(
        "The SEC requires all API requests to include an identifying User-Agent. Please fill this out to ensure data fetching works."
    )
    
    if 'app_name' not in st.session_state:
        st.session_state.app_name = "MySECApp"
    if 'email' not in st.session_state:
        st.session_state.email = "user@example.com"
        
    app_name = st.sidebar.text_input("Application Name:", key='app_name')
    email = st.sidebar.text_input("Contact Email:", key='email')
    
    # Update global HEADERS based on sidebar input
    HEADERS['User-Agent'] = f'{app_name} / {email}'
    
    st.sidebar.markdown("---")

    # --- TAB STRUCTURE ---
    tab_data, tab_daily_index, tab_lookup = st.tabs([
        "Company Filings & Metrics", 
        "Daily Filings Index",
        "CIK Lookup (Experimental)"
    ])

    # --- 1. Company Filings & Metrics Tab (Main Working Feature) ---
    with tab_data:
        st.header("Fetch Data by CIK")
        st.markdown(
            """
            Use this section to fetch detailed financial metrics and recent filings 
            for a **single company** identified by its Central Index Key (CIK).
            """
        )
        
        # CIK Input Section
        cik_input = st.text_input(
            "Central Index Key (CIK):", 
            # Use the session state variable for the value to allow CIK Lookup to update it
            value=st.session_state.target_cik, 
            max_chars=10,
            placeholder="e.g., 320193",
            key='cik_input_final' # Unique key for this input
        ).strip()
        
        if st.button("Fetch Financials & Filings"):
            target_cik = cik_input
            
            if not target_cik.isdigit():
                st.error("Please enter a valid numeric CIK to fetch data.")
                return

            # --- Data Fetching ---
            with st.spinner(f"Fetching complete data set for CIK: {target_cik}..."):
                company_data = fetch_sec_company_facts(target_cik, HEADERS)
            
            if company_data:
                company_name = company_data.get('entityName', 'N/A')
                display_identifier = f"CIK: {target_cik}"
                
                # 1. Display Filings with Filter (Now uses the fallback function)
                # Pass HEADERS and CIK so the fallback can work
                display_company_filings(company_data, target_cik, company_name, HEADERS)
                
                # 2. Display Metrics
                if 'facts' in company_data:
                    display_key_metrics(company_data, display_identifier)
                else:
                    st.warning("Structured financial facts were not available for this period.")

            st.markdown("""
                ---
                <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
            """, unsafe_allow_html=True)

    # --- 2. Daily Filings Index Tab (Non-Company Specific Feature) ---
    with tab_daily_index:
        st.header("Browse Recent SEC Filings Index")
        st.markdown(
            """
            This section loads a list of thousands of filings from a recent SEC quarterly master index file, 
            allowing you to view and filter filings across all companies.
            """
        )
        
        # --- Index Selection ---
        st.subheader("1. Load Quarterly Index")
        
        # Updated index options to be more current (assuming today is late 2025)
        index_options = {
            "2025 Q4 (Oct - Dec)": (2025, 4), # Added next quarter
            "2025 Q3 (July - Sep)": (2025, 3),
            "2025 Q2 (Apr - Jun)": (2025, 2),
            "2025 Q1 (Jan - Mar)": (2025, 1),
            "2024 Q4 (Oct - Dec)": (2024, 4),
            "2024 Q3 (July - Sep)": (2024, 3),
        }
        
        selected_key = st.selectbox(
            "Choose a Quarterly Master Index (Contains all filings for that 3-month period):",
            options=list(index_options.keys()),
            index=1, # Default to a safe, already passed quarter
            key='index_quarter_select'
        )
        
        col_load, col_clear = st.columns([1, 1])
        
        if col_load.button(f"Load Filings for {selected_key}"):
             # Use the callback to load the data
             load_master_index_callback(selected_key, index_options)
        
        if col_clear.button("Clear Loaded Data"):
            clear_master_index_callback()
            st.rerun() # Rerun to clear the display immediately

        # --- Display and Filter Loaded Data ---
        df = st.session_state.master_filings_df
        
        if not df.empty:
            
            # Make sure 'Date Filed' is datetime for filtering
            if df['Date Filed'].dtype != '<M8[ns]': # Check if not already datetime
                 df['Date Filed'] = pd.to_datetime(df['Date Filed'])

            st.markdown("---")
            st.subheader(f"2. Filter Filings from Index ({st.session_state.loaded_index_key})")
            
            # --- 2.1 Date Range Filter (New Calendar Filter) ---
            
            # Determine min/max dates in the loaded DataFrame
            min_date = df['Date Filed'].min().date()
            max_date = df['Date Filed'].max().date()
            
            # Set default range to last 14 days or the full range if the index is old
            today = date.today()
            default_start = max(min_date, today - relativedelta(weeks=2))
            default_end = max_date # Default end is the latest date in the loaded data

            date_range = st.date_input(
                f"Filter by Filing Date Range (Index Dates: {min_date} to {max_date}):",
                value=[default_start, default_end],
                min_value=min_date,
                max_value=max_date,
                key='master_date_range'
            )
            
            df_filtered = df.copy()

            if len(date_range) == 2:
                start_date = pd.to_datetime(min(date_range))
                # Add one day to the end date to include all filings on the end date
                end_date = pd.to_datetime(max(date_range)) + pd.DateOffset(days=1)
                
                # Apply date filter
                df_filtered = df[(df['Date Filed'] >= start_date) & (df['Date Filed'] < end_date)]
            
            st.caption(f"Filings matching the selected date range: {len(df_filtered):,}")

            # --- 2.2 Content Filters (Form Type and Company Name) ---
            filter_cols = st.columns(2)
            
            # Filter by Form Type
            all_forms = sorted(df['Form Type'].unique()) # Use the original df to get all possible forms
            
            default_forms = [f for f in ['10-K', '10-Q', '8-K', '4', 'S-1', 'D'] if f in all_forms]
            if not default_forms:
                # If common forms aren't present in the master file, default to the top 5 types
                default_forms = all_forms[:5]
            
            selected_forms = filter_cols[0].multiselect(
                "Filter by Form Type:",
                options=all_forms,
                default=default_forms,
                key='master_form_filter'
            )
            
            # Filter by Company Name Search
            search_query = filter_cols[1].text_input(
                "Search Company Name:",
                placeholder="e.g., Apple, Microsoft, Tesla",
                key='master_name_search'
            )
            
            # Apply Form Type Filter
            df_filtered = df_filtered[df_filtered['Form Type'].isin(selected_forms)]
            
            # Apply Company Name Filter
            if search_query:
                df_filtered = df_filtered[df_filtered['Company Name'].str.contains(search_query, case=False, na=False)]

            # 3. Display Result
            st.markdown("---")
            st.subheader(f"3. Filtered Results")
            st.caption(f"Displaying up to 500 records. Total matching records: **{len(df_filtered):,}**")
            
            # Sort by date for recent visibility and limit for display
            df_display = df_filtered.sort_values(by='Date Filed', ascending=False).head(500)[['Company Name', 'Form Type', 'Date Filed', 'Link', 'CIK']].copy()
            
            st.dataframe(
                df_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document"),
                    "CIK": st.column_config.Column("CIK", width="small")
                }
            )
        else:
             st.info("Click 'Load Filings' above to download a massive SEC quarterly index file for browsing. You must load the data before filtering.")
        
    # --- 3. CIK Lookup (Experimental) Tab ---
    with tab_lookup:
        st.header("Search CIK by Ticker (Experimental)")
        st.warning(
            """
            **ATTENTION:** This feature is highly unstable. The SEC frequently blocks 
            or changes the underlying search page, causing the ticker lookup to fail. 
            Use the CIK directly on the main tab for reliable data fetching.
            """
        )
        
        ticker_input = st.text_input(
            "Enter Stock Ticker:", 
            value="",
            max_chars=10,
            placeholder="e.g., TSLA, MSFT, AMZN",
            key='ticker_input' 
        ).strip().upper()

        st.button(
            "Search CIK", 
            on_click=search_cik_callback, 
            args=(app_name, email),
            key='search_cik_button'
        )

if __name__ == '__main__':
    main()
