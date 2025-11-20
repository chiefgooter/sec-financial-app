import streamlit as st
import requests
import json
from time import sleep

# --- Configuration ---
# You MUST provide a User-Agent string that identifies your application and includes
# an administrative contact email address. The SEC may block scripts that don't comply.
# *** IMPORTANT: Replace 'YourAppName' and 'youremail@example.com' with your actual information. ***
HEADERS = {
    'User-Agent': 'YourAppName / youremail@example.com',
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'data.sec.gov'
}

# --- Core Data Fetching Function ---

@st.cache_data(ttl=3600) # Cache the data for 1 hour to prevent excessive API calls
def fetch_sec_company_facts(cik):
    """
    Fetches the company facts JSON data from the SEC EDGAR API for a given CIK.
    Handles the required 10-digit padding for the CIK.
    """
    # Ensure CIK is padded to 10 digits
    padded_cik = str(cik).zfill(10)
    
    FACTS_URL = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json'
    
    # Implementing a small delay to respect the SEC's rate limit of 10 requests/second
    sleep(0.5) 
    
    try:
        response = requests.get(FACTS_URL, headers=HEADERS)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            st.error(f"Error: Could not find data for CIK {cik}. Please check the CIK.")
        else:
            st.error(f"HTTP Error {response.status_code}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data: {e}. Check network connection or SEC API status.")
        return None

# --- Data Analysis and Presentation Function ---

def display_key_metrics(data):
    """
    Analyzes and displays key US-GAAP metrics in a Streamlit interface.
    """
    company_name = data.get('entityName', 'N/A')
    st.subheader(f"Financial Summary for {company_name}")
    
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
    st.markdown("Enter a company's CIK to retrieve structured financial data from their public filings.")
    
    # Input for Central Index Key (CIK)
    # Using Apple (320193) as the default example CIK
    cik_input = st.text_input(
        "Enter CIK (Central Index Key):", 
        value="320193",
        max_chars=10,
        placeholder="e.g., 320193"
    ).strip()
    
    if st.button("Fetch Data"):
        if not cik_input.isdigit():
            st.error("Please enter a valid numeric CIK.")
            return

        with st.spinner(f"Fetching data for CIK {cik_input}..."):
            # Fetch data
            company_data = fetch_sec_company_facts(cik_input)
            
            if company_data and 'facts' in company_data:
                display_key_metrics(company_data)
            elif company_data:
                st.warning("Data found for this CIK, but structured US-GAAP financial facts were not available.")

        st.markdown("""
            ---
            <small>Data Source: SEC EDGAR API. CIK is required for API access.</small>
        """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()
