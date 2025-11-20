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
        # The CIK is usually located in a tag that looks like:
        # <CIK>0000320193</CIK> or in a URL like .../0000320193/..
        
        # --- Simple CIK Extraction from HTML Content ---
        # Look for the CIK number in the format 'CIK########'
        import re
        
        # Look for the CIK in the company info table, often right after 'CIK:'
        # The CIK is 10 digits padded with leading zeros.
        # We search for the pattern 'CIK' followed by optional spaces and then the 10-digit number
        match = re.search(r'CIK:\s*<a href="/cgi-bin/browse-edgar\?action=getcompany&amp;CIK=(\d{10})&amp', response.text)
        
        if match:
            found_cik = match.group(1)
            
            # Since the search API doesn't easily return the clean name, 
            # we'll use a placeholder and let the companyfacts API fill in the name later.
            return found_cik, f"Ticker Search: {ticker}" 
        
        # Fallback: Look for the 10-digit CIK string directly (less reliable)
        match_fallback = re.search(r'\b(\d{10})\b', response.text)
        if match_fallback:
            found_cik = match_fallback.group(1)
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

# --- Filings Presentation Function ---

def display_filings(data, company_name):
    """
    Displays the recent company filings by extracting data from the 'companyfacts' JSON.
    """
    st.markdown("---")
    st.header(f"Recent Filings ({company_name})")

    filings_list = data.get('listFilings', [])

    if not filings_list:
        st.warning("No recent filings data available in the company facts file.")
        return

    filings_for_df = []
    
    for filing in filings_list:
        filings_for_df.append({
            'Filing Type': filing.get('form', 'N/A'),
            'Filing Date': filing.get('filingDate', 'N/A'),
            'Description': filing.get('formDescription', 'N/A'),
            'CIK': data.get('cik'), 
            'Accession Number': filing.get('accessionNumber', 'N/A')
        })

    # Limit to the top 20 recent filings for a cleaner view
    df = pd.DataFrame(filings_for_df[:20])

    def create_sec_link(row):
        acc_no_clean = row['Accession Number'].replace('-', '')
        return f"https://www.sec.gov/Archives/edgar/data/{row['CIK']}/{acc_no_clean}/{row['Accession Number']}.txt"

    df['Link'] = df.apply(create_sec_link, axis=1)

    df_display = df[['Filing Type', 'Filing Date', 'Description', 'Link']]
    
    st.dataframe(
        df_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document")
        }
    )
    st.caption("Note: 'View Filing' links to the full submission file on SEC EDGAR.")

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
        # The get_cik_data function handles the error display if the SEC file is down
        found_cik, company_name = get_cik_data(ticker, HEADERS)
        
        if found_cik:
            st.session_state.target_cik = found_cik
            # The name from the search API is not always clean, so we only display CIK success
            st.success(f"CIK found: **{found_cik}** (Enter this CIK below or click 'Fetch Financials & Filings').")
        else:
            st.session_state.target_cik = ""
            # Error message is handled within get_cik_data if the file is down
            if not company_name: # company_name will be None if the ticker wasn't in the file
                 st.error(f"Could not find a CIK for ticker: {ticker}. Try searching by company name or CIK directly.")


# --- Streamlit Main App Layout ---

def main():
    st.set_page_config(
        page_title="SEC EDGAR Financial Data App",
        layout="centered",
        initial_sidebar_state="expanded"
    )
    
    st.title("SEC EDGAR Data Viewer")
    st.markdown("Search by Ticker to find the CIK, or search directly with the CIK.")
    
    # --- Sidebar for SEC Compliance ---
    st.sidebar.header("SEC Compliance (Required)")
    st.sidebar.markdown(
        "The SEC requires all API requests to include a identifying User-Agent. Please fill this out to ensure data fetching works."
    )
    
    # Store compliance inputs in session state to persist them better across runs
    if 'app_name' not in st.session_state:
        st.session_state.app_name = "MySECApp"
    if 'email' not in st.session_state:
        st.session_state.email = "user@example.com"
        
    app_name = st.sidebar.text_input("Application Name:", key='app_name')
    email = st.sidebar.text_input("Contact Email:", key='email')
    
    # Update global HEADERS based on sidebar input
    HEADERS['User-Agent'] = f'{app_name} / {email}'
    
    st.sidebar.markdown("---")
    
    # --- Ticker Search Section (New independent flow) ---
    st.header("1. Search CIK by Ticker")
    st.caption("Uses the SEC search engine (most reliable CIK lookup method).")
    ticker_input = st.text_input(
        "Enter Stock Ticker:", 
        value="",
        max_chars=10,
        placeholder="e.g., TSLA, MSFT, AMZN",
        key='ticker_input' # Key to read input inside callback
    ).strip().upper()

    # The search button triggers the callback to update st.session_state.target_cik
    st.button(
        "Search CIK", 
        on_click=search_cik_callback, 
        args=(app_name, email)
    )
    
    st.markdown("---")

    # --- CIK Input Section (Now linked to session state) ---
    st.header("2. Fetch Data by CIK")
    # This CIK input now uses the value stored in session state, which gets updated by the ticker search
    cik_input = st.text_input(
        "Central Index Key (CIK):", 
        value=st.session_state.target_cik, 
        max_chars=10,
        placeholder="e.g., 320193",
        key='cik_input_final' # Unique key for the final input box
    ).strip()
    
    if st.button("Fetch Financials & Filings"):
        
        target_cik = cik_input
        
        if not target_cik.isdigit():
            st.error("Please enter a valid numeric CIK to fetch data.")
            return

        # --- Data Fetching ---
        # --- Fetch Structured Financial Facts & Filings Data in one go ---
        with st.spinner(f"Fetching complete data set for CIK: {target_cik}..."):
            company_data = fetch_sec_company_facts(target_cik, HEADERS)
        
        if company_data:
            # Use the name from the facts data
            company_name = company_data.get('entityName', 'N/A')
            
            # Identifier is the CIK here, as it's the most reliable identifier used for the fetch
            display_identifier = f"CIK: {target_cik}"
            
            # 1. Display Filings (extracted from company_data)
            display_filings(company_data, company_name)
            
            # 2. Display Metrics (extracted from company_data)
            if 'facts' in company_data:
                display_key_metrics(company_data, display_identifier)
            else:
                st.warning("Structured financial facts were not available for this period.")

        st.markdown("""
            ---
            <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
        """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()
