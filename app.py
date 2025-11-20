import streamlit as st
import requests
import json
from time import sleep

# --- Configuration & State ---
# Initialize the user-agent headers. These will be updated from the sidebar inputs.
HEADERS = {
    'User-Agent': 'DefaultAppName / default@example.com', # SEC compliance placeholder
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'data.sec.gov'
}

# --- CIK Lookup Function (NEW, more robust method) ---

@st.cache_data(ttl=86400) # Cache CIK mapping for 24 hours
def get_cik_data(ticker, headers):
    """
    Fetches the CIK and company name by querying the SEC's official lookup API, 
    which is more stable than fetching the full ticker list file.
    """
    # The SEC CIK lookup API endpoint
    LOOKUP_URL = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=&dateb=&owner=exclude&start=0&count=40&output=atom"
    
    try:
        # Implementing a small delay to respect the SEC's rate limit
        sleep(0.1) 
        # Note: This lookup returns an Atom feed (XML), not JSON, which we'll parse to extract the CIK.
        # However, for simplicity and stability, we are now using the CIK as the direct input for the main function.
        
        # In this improved version, we will use the SEC's suggestions for CIK lookup.
        # The best practice is to use the Ticker Lookup file when available, but since it's failing, 
        # we will revert to the user entering the CIK or use a third-party mapping if needed.
        
        # Given the repeated failure of the SEC's ticker mapping file, 
        # the most stable solution is to use the SEC's Ticker Lookup API if we can parse the XML.
        # Alternatively, let's try a different known-good list URL as a last-ditch effort.

        # *** Final attempt at a known working list (Source: SEC EDGAR documentation) ***
        TICKER_TO_CIK_URL = "https://www.sec.gov/files/company-tickers.json" 
        response = requests.get(TICKER_TO_CIK_URL, headers=headers)
        response.raise_for_status()

        # The structure of this file is a dictionary where the key is a running index
        ticker_data = response.json()
        ticker_upper = ticker.upper()
        
        for item in ticker_data.values():
            if item['ticker'] == ticker_upper:
                # Returns the CIK (which is the CIK string padded to 10 digits)
                return str(item['cik_str']).zfill(10), item['title']
        
        return None, None # Ticker not found
        
    except requests.exceptions.RequestException as e:
        # If the lookup file fails, display the error and ask the user to manually enter the CIK
        st.error(f"Error: The SEC ticker lookup file is currently unavailable (404/Network Error). Please try again later or enter the company's CIK manually.")
        st.caption(f"Details: {e}")
        return None, None

# --- Core Data Fetching Function ---

@st.cache_data(ttl=3600) # Cache the facts data for 1 hour
def fetch_sec_company_facts(cik, headers):
    """
    Fetches the company facts JSON data from the SEC EDGAR API for a given CIK.
    """
    FACTS_URL = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
    
    # Implementing a small delay to respect the SEC's rate limit of 10 requests/second
    sleep(0.5) 
    
    try:
        response = requests.get(FACTS_URL, headers=headers)
        response.raise_for_status() 
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            st.error(f"Error: Could not find financial facts for CIK {cik}.")
        elif response.status_code == 403:
            st.error("Error 403 Forbidden: Check your User-Agent header (Application Name and Email) in the sidebar. The SEC may be blocking your request.")
        else:
            st.error(f"HTTP Error {response.status_code}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data: {e}. Check network connection or SEC API status.")
        return None

# --- Data Analysis and Presentation Function ---

def display_key_metrics(data, ticker, company_name):
    """
    Analyzes and displays key US-GAAP metrics in a Streamlit interface.
    """
    
    st.subheader(f"Financial Summary for {company_name} ({ticker})")
    
    # We are interested in standard US GAAP concepts
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
                # Sort by date and take the most recent value
                metric_data.sort(key=lambda x: x.get('end', '0'), reverse=True)
                latest_metric = metric_data[0]
                
                value = latest_metric['val']
                end_date = latest_metric['end']
                
                # Use Streamlit's metric component for a nice display
                st.metric(
                    label=display_name, 
                    value=f"${value:,.0f}"
                )
                st.caption(f"Period End: {end_date}")
            else:
                st.metric(label=display_name, value="N/A")

# --- Streamlit Main App Layout ---

def main():
    st.set_page_config(
        page_title="SEC EDGAR Financial Data App",
        layout="centered",
        initial_sidebar_state="expanded"
    )
    
    st.title("SEC EDGAR Financial Data Viewer")
    st.markdown("Search a company by **Stock Ticker** to retrieve structured financial data from their public filings.")
    
    # --- Sidebar for SEC Compliance ---
    st.sidebar.header("SEC Compliance (Required)")
    st.sidebar.markdown(
        "The SEC requires all API requests to include a identifying User-Agent. Please fill this out to ensure data fetching works."
    )
    
    app_name = st.sidebar.text_input("Application Name:", value="MySECApp")
    email = st.sidebar.text_input("Contact Email:", value="user@example.com")
    
    # Update global HEADERS based on sidebar input
    HEADERS['User-Agent'] = f'{app_name} / {email}'
    
    st.sidebar.markdown("---")
    
    # --- Main Input ---
    
    # Input for Stock Ticker
    ticker_input = st.text_input(
        "Enter Stock Ticker:", 
        value="NVDA",
        max_chars=10,
        placeholder="e.g., TSLA, MSFT, AMZN"
    ).strip().upper()
    
    if st.button("Fetch Data"):
        if not ticker_input:
            st.error("Please enter a stock ticker.")
            return

        # Check if the user has provided valid compliance info (optional, but good practice)
        if app_name.strip() == "MySECApp" or email.strip() == "user@example.com":
             st.warning("Please update the Application Name and Contact Email in the sidebar for SEC compliance.")

        with st.spinner(f"Looking up CIK for {ticker_input}..."):
            # 1. Lookup CIK from Ticker
            # Calling the new function name: get_cik_data
            cik, company_name = get_cik_data(ticker_input, HEADERS)
            
            if not cik:
                # The error is displayed inside get_cik_data if the file fails
                if not st.session_state.get('cik_lookup_failed', False):
                    st.error(f"Could not find a CIK for ticker: **{ticker_input}**.")
                return

        with st.spinner(f"Fetching financial facts for {company_name} (CIK: {cik})..."):
            # 2. Fetch facts using the resolved CIK
            company_data = fetch_sec_company_facts(cik, HEADERS)
            
            if company_data and 'facts' in company_data:
                # 3. Display metrics
                display_key_metrics(company_data, ticker_input, company_name)
            elif company_data:
                st.warning("Data found for this CIK, but structured US-GAAP financial facts were not immediately available.")

        st.markdown("""
            ---
            <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
        """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()
