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
if 'loaded_index_key' not in st.session_state: 
    st.session_state.loaded_index_key = ""

# NEW: Initialize the Watchlist state {CIK: Company Name}
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = {
        "320193": "APPLE INC.", 
        "0001652044": "ALPHABET INC.",
    }

# --- WATCHLIST CRUD FUNCTIONS (Using Session State for Persistence Simulation) ---

def add_to_watchlist(cik, company_name):
    """Adds a CIK and company name to the session state watchlist."""
    # Ensure CIK is padded to 10 digits
    padded_cik = str(cik).zfill(10)
    
    # Check if already exists
    if padded_cik in st.session_state.watchlist:
        st.warning(f"**{company_name}** (CIK: {padded_cik}) is already in your Watchlist!")
        return

    # Add to watchlist (simulated database save)
    st.session_state.watchlist[padded_cik] = company_name
    st.success(f"Added **{company_name}** to Watchlist. (Current Count: {len(st.session_state.watchlist)})")

def remove_from_watchlist(cik):
    """Removes a CIK from the session state watchlist."""
    padded_cik = str(cik).zfill(10)
    if padded_cik in st.session_state.watchlist:
        company_name = st.session_state.watchlist.pop(padded_cik)
        st.toast(f"Removed {company_name} from Watchlist.")
    # Force a re-run to refresh the Watchlist table display
    st.rerun()

# --- CALLBACK FUNCTIONS ---

def update_target_cik(cik):
    """Callback to set the CIK input and main CIK state for the other tab."""
    st.session_state.cik_input_final = str(cik).zfill(10)
    st.session_state.target_cik = str(cik).zfill(10)
    st.toast(f"CIK {cik} copied to the 'Company Filings' tab!")

# --- CIK Lookup Function (Using Search API for Stability) ---
@st.cache_data(ttl=86400) # Cache CIK mapping for 24 hours to reduce load on SEC
def get_cik_data(ticker, headers):
    """
    Attempts to fetch the CIK and company name using the SEC's EDGAR full-text search.
    """
    SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    params = {
        'action': 'getcompany',
        'Company': ticker,
        'output': 'xml'
    }

    try:
        sleep(0.5) 
        response = requests.get(SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()

        found_cik = None
        
        # --- ROBUST CIK EXTRACTION ---
        cik_match_1 = re.search(r'/edgar/data/(\d{10})/', response.text)
        if cik_match_1:
            found_cik = cik_match_1.group(1)
        
        if not found_cik:
             cik_match_2 = re.search(r'CIK:[^>]*?(\d{1,10})', response.text)
             if cik_match_2:
                found_cik = cik_match_2.group(1).zfill(10)
        
        if found_cik:
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

# --- Secondary Filing List Fetcher (More robust than facts API 'listFilings') ---

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
            # st.warning(f"Could not find the filing list table on the EDGAR page for CIK {cik}.")
            return []

        # Simple way to get the company name from the table caption/header if not available elsewhere
        company_name = soup.find('span', class_='companyName').text.split('CIK')[0].strip() if soup.find('span', class_='companyName') else f"CIK: {cik}"

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
                        'Company Name': company_name, # Added company name
                        'CIK': padded_cik, # Added CIK
                        'Filing Type': form_type,
                        'Filing Date': date_filed,
                        'Link': document_link,
                        'Description': 'Link to Index File' # Simple description for this method
                    })
        
        return filings_data

    except requests.exceptions.RequestException as e:
        # st.error(f"Error fetching filings from EDGAR page for CIK {cik}: {e}")
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
        
        # Use StringIO to treat the raw text as a file buffer for pandas to read
        data = StringIO("\n".join(data_lines))
        # Use a consistent separator ('|') and define column names
        df = pd.read_csv(data, sep='|', header=None, 
                         names=['CIK', 'Company Name', 'Form Type', 'Date Filed', 'Filename'])
        
        # Convert CIK to string and pad it to 10 digits for consistency
        df['CIK'] = df['CIK'].astype(str).str.zfill(10)
        
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
        "NetIncomeLoss": "Latest Reported Net Income / Loss",
        "EarningsPerShareBasic": "Latest EPS (Basic)"
    }

    cols = st.columns(len(metrics_to_find))
    
    for i, (gaap_tag, display_name) in enumerate(metrics_to_find.items()):
        
        units_data = us_gaap.get(gaap_tag, {}).get('units', {})
        
        # Prioritize USD, then shares for EPS, otherwise the first available unit.
        metric_data = units_data.get('USD', [])
        if not metric_data and gaap_tag == "EarningsPerShareBasic":
            metric_data = units_data.get('shares', [])
        if not metric_data and units_data:
            metric_data = next(iter(units_data.values()))


        with cols[i]:
            if metric_data:
                # Sort by end date (latest first)
                metric_data.sort(key=lambda x: x.get('end', '0'), reverse=True)
                latest_metric = metric_data[0]
                
                value = latest_metric['val']
                end_date = latest_metric['end']
                
                # Format value based on type (simple heuristic)
                if gaap_tag == "EarningsPerShareBasic":
                    value_str = f"${value:,.2f}" # EPS usually has decimals
                else:
                    value_str = f"${value:,.0f}" # Revenue/Assets/NetIncome in whole dollars

                st.metric(label=display_name, value=value_str)
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
        st.caption("Using filing data from the structured 'facts' API (may be incomplete).")
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
        st.info("Structured filing list missing from the facts API. Falling back to EDGAR document search.")
        with st.spinner(f"Fetching list of recent filings from the main EDGAR search page for CIK {cik}..."):
            filings_for_df = fetch_edgar_filings_list(cik, headers)
        
        if not filings_for_df:
            st.warning("No recent filings data available from any source.")
            return

    df = pd.DataFrame(filings_for_df)
    
    # Identify all available filing types
    all_filing_types = set(df['Filing Type'].unique())
    
    # 1. Add Filing Type Filter
    default_selection = [t for t in ['10-K', '10-Q', '8-K', '4', 'S-1'] if t in all_filing_types]
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

    # Check for placeholder compliance values
    if app_name.strip() == "DefaultAppName" or email.strip() == "default@example.com":
         st.warning("Please update the Application Name and Contact Email in the sidebar for SEC compliance.")

    with st.spinner(f"Attempting ticker lookup for {ticker}..."):
        found_cik, company_name = get_cik_data(ticker, HEADERS)
        
        if found_cik:
            # Update the main CIK input and the target_cik state
            st.session_state.cik_input_final = found_cik 
            st.session_state.target_cik = found_cik
            st.success(f"CIK found: **{found_cik}** (Company: {company_name}). Switch to the 'Company Filings & Metrics' tab to use it.")
            # Store found name for 'Add to Watchlist' button
            st.session_state.last_searched_name = company_name
        else:
            st.session_state.target_cik = ""
            st.session_state.last_searched_name = ""
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
        
        # Clear existing data before loading new data to prevent mixed states
        st.session_state.master_filings_df = pd.DataFrame() 
        
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
    
    # --- Sidebar for SEC Compliance and Watchlist Management ---
    
    # --- 1. Compliance Section
    st.sidebar.header("SEC Compliance (Required)")
    st.sidebar.markdown(
        "The SEC requires all API requests to include an identifying User-Agent. Please fill this out to ensure data fetching works."
    )
    
    # Set default values for User-Agent
    if 'app_name' not in st.session_state:
        st.session_state.app_name = "DefaultAppName"
    if 'email' not in st.session_state:
        st.session_state.email = "default@example.com"
        
    app_name = st.sidebar.text_input("Application Name:", key='app_name')
    email = st.sidebar.text_input("Contact Email:", key='email')
    
    # Update global HEADERS based on sidebar input
    HEADERS['User-Agent'] = f'{app_name} / {email}'
    
    st.sidebar.markdown("---")

    # --- 2. Watchlist Management Section (New Sidebar Feature)
    st.sidebar.header("My Watchlist")
    
    # Watchlist Display/Removal
    if st.session_state.watchlist:
        st.sidebar.caption(f"Currently tracking **{len(st.session_state.watchlist)}** companies:")
        
        # Create a simple table or list for display and removal
        watchlist_df = pd.DataFrame(st.session_state.watchlist.items(), columns=['CIK', 'Company'])
        watchlist_df['Remove'] = '🗑️'
        
        # Streamlit component for removal action
        edit_container = st.sidebar.container()
        
        # Allow removal from the sidebar
        for cik, company_name in st.session_state.watchlist.items():
            col1, col2 = st.sidebar.columns([3, 1])
            col1.markdown(f"**{company_name}**")
            col2.button("Remove", key=f"remove_{cik}", on_click=remove_from_watchlist, args=(cik,))
        
    else:
        st.sidebar.info("Your watchlist is empty. Add companies from the 'Company Filings' tab.")

    st.sidebar.markdown("---")


    # --- TAB STRUCTURE ---
    tab_data, tab_watchlist, tab_daily_index, tab_lookup = st.tabs([
        "Company Filings & Metrics", 
        "My Watchlist Filings", # New Tab
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
        col_cik, col_add = st.columns([5, 1])
        
        cik_input = col_cik.text_input(
            "Central Index Key (CIK):", 
            value=st.session_state.target_cik, 
            max_chars=10,
            placeholder="e.g., 320193",
            key='cik_input_final', # Unique key for this input
            label_visibility="collapsed" # Hide label for better alignment
        ).strip()
        
        # Add to Watchlist Button
        if cik_input and cik_input.isdigit():
            # Try to use the company name from the last search, or look it up quickly if needed
            company_name_to_add = st.session_state.watchlist.get(cik_input)
            if not company_name_to_add:
                 # Check if the name was set by the CIK lookup in the other tab
                 company_name_to_add = st.session_state.get('last_searched_name', f"CIK {cik_input}")

            col_add.button(
                "Add to Watchlist", 
                key='add_to_watchlist_button',
                on_click=add_to_watchlist,
                args=(cik_input, company_name_to_add),
                help="Add the current company to your personal watchlist for tracking.",
                use_container_width=True
            )
        
        if st.button("Fetch Financials & Filings", key='fetch_single_cik_button'):
            target_cik = cik_input
            
            if not target_cik.isdigit():
                st.error("Please enter a valid numeric CIK to fetch data.")
                return

            # Check for placeholder compliance values before fetching
            if app_name.strip() == "DefaultAppName" or email.strip() == "default@example.com":
                 st.warning("Please update the Application Name and Contact Email in the sidebar for SEC compliance.")

            # --- Data Fetching ---
            with st.spinner(f"Fetching complete data set for CIK: {target_cik}..."):
                company_data = fetch_sec_company_facts(target_cik, HEADERS)
            
            if company_data:
                company_name = company_data.get('entityName', 'N/A')
                display_identifier = f"CIK: {target_cik}"
                
                # Set the last searched name for the 'Add to Watchlist' button
                st.session_state.last_searched_name = company_name

                # 1. Display Filings with Filter (Now uses the fallback function)
                display_company_filings(company_data, target_cik, company_name, HEADERS)
                
                # 2. Display Metrics
                if 'facts' in company_data:
                    st.markdown("---")
                    display_key_metrics(company_data, display_identifier)
                else:
                    st.warning("Structured financial facts were not available for this period.")

            st.markdown("""
                ---
                <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
            """, unsafe_allow_html=True)

    # --- 2. My Watchlist Filings Tab (New Feature) ---
    with tab_watchlist:
        st.header("My Watchlist Filings")
        st.markdown(
            """
            This table displays the **most recent filings** for all companies currently in your Watchlist. 
            The filings are aggregated and sorted by date.
            """
        )
        
        watchlist_ciks = st.session_state.watchlist.keys()

        if not watchlist_ciks:
            st.info("Your Watchlist is empty. Add companies in the sidebar or from the 'Company Filings & Metrics' tab.")
        else:
            if st.button(f"Fetch Filings for {len(watchlist_ciks)} Companies", key='fetch_watchlist_button'):
                
                all_filings = []
