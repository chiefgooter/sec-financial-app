import streamlit as st
import requests
import json
import pandas as pd
from time import sleep
from io import StringIO
import re

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
        cik_match_1 = re.search(r'CIK:[^>]*?(\d{10})', response.text)
        
        if cik_match_1:
            found_cik = cik_match_1.group(1)
            
        # --- ROBUST CIK EXTRACTION (Attempt 2: Direct URL link) ---
        if not found_cik:
             cik_match_2 = re.search(r'/edgar/data/(\d{10})/', response.text)
             if cik_match_2:
                found_cik = cik_match_2.group(1)
        
        if found_cik:
            return found_cik, f"Ticker Search: {ticker}" 
        
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

# --- Function to fetch a recent Master Index File ---

@st.cache_data(ttl=86400) # Cache the massive index file for 24 hours
def fetch_master_index_filings(year, qtr, headers):
    """
    Fetches and parses a quarterly master index file containing thousands of filings.
    This provides a non-company-specific list of filings.
    """
    # NOTE: Using a fixed, known recent quarter's URL for stability. 
    # Generating the current day's index link dynamically is very complex and fragile.
    MASTER_INDEX_URL = f'https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/master.idx'
    
    st.info(f"Fetching SEC Master Index for {year} Q{qtr}. This file contains thousands of filings and may take a moment to load.")
    
    sleep(0.5)
    
    try:
        # Host header must be correct for the archives.sec.gov domain
        data_headers = headers.copy()
        data_headers['Host'] = 'www.sec.gov' # Still use sec.gov host for this endpoint
        
        response = requests.get(MASTER_INDEX_URL, headers=data_headers)
        response.raise_for_status() 
        
        # The index file has fixed-width columns but is generally comma-separated after the header
        content = response.text
        
        # Skip the first 10 lines of header information
        content_lines = content.splitlines()
        data_lines = content_lines[11:] 
        
        # Read the data into a DataFrame
        data = StringIO("\n".join(data_lines))
        df = pd.read_csv(data, sep='|', header=None, 
                         names=['CIK', 'Company Name', 'Form Type', 'Date Filed', 'Filename'])
        
        # Create the full SEC URL link
        def create_index_sec_link(row):
             return f"https://www.sec.gov/Archives/{row['Filename']}"

        df['Link'] = df.apply(create_index_sec_link, axis=1)

        return df
        
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching Master Index for {year} Q{qtr}: {e}")
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

# --- Company Filings Presentation Function (NOW INCLUDES FILTERING) ---

def display_company_filings(data, company_name):
    """
    Displays the recent company filings by extracting data from the 'companyfacts' JSON,
    and allows filtering by filing type.
    """
    st.markdown("---")
    st.header(f"Recent Filings ({company_name})")

    filings_list = data.get('listFilings', [])

    if not filings_list:
        st.warning("No recent filings data available in the company facts file.")
        return

    filings_for_df = []
    all_filing_types = set()
    
    for filing in filings_list:
        filing_type = filing.get('form', 'N/A')
        all_filing_types.add(filing_type)
        filings_for_df.append({
            'Filing Type': filing_type,
            'Filing Date': filing.get('filingDate', 'N/A'),
            'Description': filing.get('formDescription', 'N/A'),
            'CIK': data.get('cik'), 
            'Accession Number': filing.get('accessionNumber', 'N/A')
        })

    df = pd.DataFrame(filings_for_df)
    
    # 1. Add Filing Type Filter
    default_selection = [t for t in ['10-K', '10-Q', '8-K'] if t in all_filing_types]
    
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

    def create_sec_link(row):
        acc_no_clean = row['Accession Number'].replace('-', '')
        return f"https://www.sec.gov/Archives/edgar/data/{row['CIK']}/{acc_no_clean}/{row['Accession Number']}.txt"

    df_display['Link'] = df_display.apply(create_sec_link, axis=1)

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
            st.session_state.target_cik = found_cik
            st.success(f"CIK found: **{found_cik}**. Switch to the 'Company Filings & Metrics' tab to use it.")
        else:
            st.session_state.target_cik = ""
            if not company_name:
                 st.error(f"Could not find a CIK for ticker: {ticker}. This feature is experimental due to SEC instability.")


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
            value=st.session_state.target_cik, 
            max_chars=10,
            placeholder="e.g., 320193",
            key='cik_input_final'
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
                
                # 1. Display Filings with Filter
                display_company_filings(company_data, company_name)
                
                # 2. Display Metrics
                if 'facts' in company_data:
                    display_key_metrics(company_data, display_identifier)
                else:
                    st.warning("Structured financial facts were not available for this period.")

            st.markdown("""
                ---
                <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
            """, unsafe_allow_html=True)

    # --- 2. Daily Filings Index Tab (New Non-Company Specific Feature) ---
    with tab_daily_index:
        st.header("Browse Recent SEC Filings Index")
        st.markdown(
            """
            This section loads a list of thousands of filings from a recent SEC master index file, 
            allowing you to view and filter filings across all companies.
            """
        )
        
        # --- Index Selection (Hardcoded for stability, but allowing choice) ---
        st.subheader("Select Index Quarter")
        
        # The SEC is currently in Q4 2024, so let's offer a few recent quarters.
        index_options = {
            "2024 Q3 (July - Sep)": (2024, 3),
            "2024 Q2 (Apr - Jun)": (2024, 2),
            "2024 Q1 (Jan - Mar)": (2024, 1),
        }
        
        selected_key = st.selectbox(
            "Choose a Quarterly Master Index:",
            options=list(index_options.keys()),
            index=0 # Default to the most recent option
        )
        
        year, qtr = index_options[selected_key]
        
        if st.button(f"Load Filings for {selected_key}"):
            with st.spinner(f"Loading and parsing massive master index for {selected_key}..."):
                master_df = fetch_master_index_filings(year, qtr, HEADERS)
            
            if not master_df.empty:
                st.session_state.master_filings_df = master_df
            else:
                st.error("Could not load the Master Index file.")
                st.session_state.master_filings_df = pd.DataFrame()

        # --- Display and Filter Loaded Data ---
        if 'master_filings_df' in st.session_state and not st.session_state.master_filings_df.empty:
            df = st.session_state.master_filings_df
            
            st.markdown("---")
            st.subheader(f"Filings Found: {len(df):,}")
            
            # 1. Filtering controls
            filter_cols = st.columns(2)
            
            # Filter by Form Type
            all_forms = sorted(df['Form Type'].unique())
            default_forms = [f for f in ['10-K', '10-Q', '8-K', '4'] if f in all_forms]
            
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
            
            df_filtered = df[df['Form Type'].isin(selected_forms)]
            
            if search_query:
                df_filtered = df_filtered[df_filtered['Company Name'].str.contains(search_query, case=False, na=False)]

            # 2. Display Result
            st.caption(f"Displaying up to 500 records. Total matching records: {len(df_filtered):,}")
            
            df_display = df_filtered.head(500)[['Company Name', 'Form Type', 'Date Filed', 'Link', 'CIK']].copy()
            
            st.dataframe(
                df_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document"),
                    "CIK": st.column_config.Column("CIK", width="small")
                }
            )
        
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
