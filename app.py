import streamlit as st
import requests
import json
import pandas as pd
from time import sleep

# --- Configuration & State ---
# Initialize the user-agent headers. These will be updated from the sidebar inputs.
HEADERS = {
    'User-Agent': 'DefaultAppName / default@example.com', # SEC compliance placeholder
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'www.sec.gov' # Changed host to sec.gov for search API
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
    This bypasses the highly unstable company_tickers.json file.
    """
    # Use the SEC's general search API for a more stable lookup
    SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    params = {
        'action': 'getcompany',
        'Company': ticker,
        'output': 'xml' # Requesting XML output for easier parsing (it's actually a form of HTML)
    }

    try:
        sleep(0.5) 
        # Note: headers are adapted for the search endpoint
        response = requests.get(SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()

        # The response is HTML/XML, not JSON. We must parse it.
        import re
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
            # Since the search API doesn't easily return the clean name, 
            # we'll use a placeholder and let the companyfacts API fill in the name later.
            return found_cik, f"Ticker Search: {ticker}" 
        
        # Ticker not found
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
    This endpoint also contains the recent filings list.
    """
    padded_cik = str(cik).zfill(10)
    FACTS_URL = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json'
    
    # Implementing a small delay to respect the SEC's rate limit of 10 requests/second
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

# --- Filings Presentation Function (NOW INCLUDES FILTERING) ---

def display_filings(data, company_name):
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
    # Default to showing 10-K, 10-Q, and 8-K if they exist
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
    st.caption(f"Showing {len(df_final)} of the most recent filtered filings.")

# --- CALLBACK FUNCTION FOR TICKET SEARCH ---
def search_cik_callback(app_name, email):
    """
    Callback function executed when the 'Search CIK' button is pressed.
    It performs the ticker lookup and updates st.session_state.target_cik.
    """
    ticker = st.session_state.ticker_input.strip().upper()
    
    if not ticker:
        st.error("Please enter a Stock Ticker to search.")
        st.session_state.target_cik = ""
        return

    # --- Compliance check ---
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
        page_title="SEC EDGAR Financial Data App",
        layout="centered",
        initial_sidebar_state="expanded"
    )
    
    st.title("SEC EDGAR Data Viewer")
    
    # --- Sidebar for SEC Compliance ---
    st.sidebar.header("SEC Compliance (Required)")
    st.sidebar.markdown(
        "The SEC requires all API requests to include a identifying User-Agent. Please fill this out to ensure data fetching works."
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
    tab_data, tab_lookup = st.tabs(["Company Filings & Metrics", "CIK Lookup (Experimental)"])

    # --- 1. Company Filings & Metrics Tab (Main Working Feature) ---
    with tab_data:
        st.header("Fetch Data by CIK")
        st.markdown(
            """
            This section uses the stable SEC API to fetch financial metrics and filings 
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
                display_filings(company_data, company_name)
                
                # 2. Display Metrics
                if 'facts' in company_data:
                    display_key_metrics(company_data, display_identifier)
                else:
                    st.warning("Structured financial facts were not available for this period.")

            st.markdown("""
                ---
                <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
            """, unsafe_allow_html=True)

    # --- 2. CIK Lookup (Experimental) Tab ---
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
