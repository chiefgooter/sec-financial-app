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
import numpy as np # Used for handling NaN in DataFrame operations

# --- Firebase Imports (Needed for Watchlists) ---
# Global variables are provided by the canvas environment
try:
    import firebase_admin
    from firebase_admin import initialize_app, firestore, credentials
    # Note: Using credentials.Certificate is often the source of 'black screen' errors 
    # when the key is not provided. We will fix the initialization below.
except ImportError:
    st.error("Firebase Admin SDK is not installed. Please add 'firebase-admin' to requirements.txt.")
    st.stop()


# --- Configuration & State ---
# Initialize the user-agent headers. These will be updated from the sidebar inputs.
HEADERS = {
    'User-Agent': 'DefaultAppName / default@example.com', # SEC compliance placeholder
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'www.sec.gov'
}

# Base URLs
CIK_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSION_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Initialize session state for CIK storage and Watchlist
if 'target_cik' not in st.session_state:
    st.session_state.target_cik = "320193" # Default CIK for Apple
if 'master_filings_df' not in st.session_state:
    st.session_state.master_filings_df = pd.DataFrame()
if 'loaded_index_key' not in st.session_state: 
    st.session_state.loaded_index_key = ""
if 'watchlists' not in st.session_state:
    st.session_state.watchlists = {} # {list_name: {cik: name}}
if 'selected_watchlist' not in st.session_state:
    st.session_state.selected_watchlist = None
if 'firebase_initialized' not in st.session_state:
    st.session_state.firebase_initialized = False
if 'db' not in st.session_state:
    st.session_state.db = None
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'watchlists_loading' not in st.session_state:
    st.session_state.watchlists_loading = False

# --- FIREBASE INITIALIZATION HANDLER (FIXED) ---

def initialize_firebase():
    """Initializes Firebase and authenticates the user."""
    if st.session_state.firebase_initialized:
        return True

    try:
        # Load config and app ID from global environment variables
        app_id = globals().get('__app_id', 'default-app-id')
        firebase_config = json.loads(globals().get('__firebase_config', '{}'))
        initial_auth_token = globals().get('__initial_auth_token', None)
        
        if not firebase_config or not firebase_config.get('projectId'):
            st.warning("Firebase config is missing. Watchlist feature will not be persistent.")
            st.session_state.db = None
            return False

        # --- CRITICAL FIX START ---
        # Instead of using credentials.Certificate with dummy data (which causes a black screen crash),
        # we try to initialize the app relying on environment credentials or use the provided 
        # project ID in a safe manner, handling potential re-initialization errors.

        try:
            # Check if app is already initialized. Use get_app to retrieve.
            app = firebase_admin.get_app(name=app_id)
        except ValueError:
            # If not initialized, try to initialize it without explicit credentials,
            # relying on the platform's execution context for authentication.
            app = initialize_app(name=app_id) 
        except Exception as e:
            st.error(f"Failed to initialize Firebase app: {e}")
            st.session_state.db = None
            return False
        # --- CRITICAL FIX END ---

        st.session_state.db = firestore.client(app=app)
        st.session_state.app_id = app_id
        
        # Determine User ID (simulated using the provided auth token)
        if initial_auth_token:
            # Placeholder logic to derive a consistent UID from the token for Firestore
            st.session_state.user_id = "canvas_user_" + str(hash(initial_auth_token) % 1000000)
        else:
            # Anonymous or default user
            st.session_state.user_id = "anon_user" 

        st.session_state.firebase_initialized = True
        return True

    except Exception as e:
        st.error(f"Error initializing Firebase. Watchlists will not be saved: {e}")
        st.session_state.db = None
        return False

# --- WATCHLIST HANDLERS ---

def get_watchlist_collection_ref():
    """Returns the Firestore collection reference for the user's private watchlists."""
    if not st.session_state.db or not st.session_state.user_id:
        return None
        
    app_id = st.session_state.app_id
    user_id = st.session_state.user_id
    
    # Private data path: /artifacts/{appId}/users/{userId}/watchlists
    collection_path = f"artifacts/{app_id}/users/{user_id}/watchlists"
    return st.session_state.db.collection(collection_path)


@st.cache_data(ttl=600) # Cache watchlist data for 10 minutes
def load_watchlists():
    """Loads all watchlists for the current user from Firestore."""
    if st.session_state.watchlists_loading:
        return st.session_state.watchlists
        
    st.session_state.watchlists_loading = True
    
    col_ref = get_watchlist_collection_ref()
    if not col_ref:
        st.session_state.watchlists_loading = False
        return {}

    try:
        docs = col_ref.stream()
        watchlists = {}
        for doc in docs:
            data = doc.to_dict()
            if 'companies' in data:
                watchlists[doc.id] = data['companies']
        
        st.session_state.watchlists = watchlists
        st.session_state.watchlists_loading = False
        return watchlists

    except Exception as e:
        st.error(f"Error loading watchlists: {e}")
        st.session_state.watchlists_loading = False
        return {}

def save_watchlist(name, companies):
    """Saves or updates a single watchlist to Firestore."""
    col_ref = get_watchlist_collection_ref()
    if not col_ref:
        st.error("Database connection not available to save watchlist.")
        return False
        
    try:
        doc_ref = col_ref.document(name)
        doc_ref.set({"companies": companies})
        st.toast(f"Watchlist '{name}' saved successfully!")
        load_watchlists.clear() # Clear cache to force reload
        st.session_state.watchlists[name] = companies
        return True
    except Exception as e:
        st.error(f"Error saving watchlist '{name}': {e}")
        return False

def add_company_to_watchlist_callback():
    """Callback to add a company to the currently selected watchlist."""
    # Renamed key for clarity
    list_name = st.session_state.watchlist_select_box 
    cik_to_add = st.session_state.company_cik_input.strip()
    company_name_input = st.session_state.company_name_input.strip()

    if not list_name or list_name == "<No Watchlists>":
        st.warning("Please select or create a watchlist first.")
        return

    if not cik_to_add or not cik_to_add.isdigit():
        st.warning("Please enter a valid CIK (Central Index Key).")
        return

    # 1. Update the local state
    current_list = st.session_state.watchlists.get(list_name, {})
    # Pad CIK to 10 digits as required by SEC filing data
    current_list[cik_to_add.zfill(10)] = company_name_input or f"CIK {cik_to_add.zfill(10)}"
    st.session_state.watchlists[list_name] = current_list
    
    # 2. Save to Firestore
    if save_watchlist(list_name, current_list):
        st.toast(f"Added {company_name_input} to '{list_name}'.")
        st.session_state.company_cik_input = ""
        st.session_state.company_name_input = "" # Clear inputs

def create_watchlist_callback():
    """Callback to create a new watchlist."""
    new_name = st.session_state.new_watchlist_name.strip()
    if not new_name:
        st.error("Watchlist name cannot be empty.")
        return
    if new_name in st.session_state.watchlists:
        st.error(f"Watchlist '{new_name}' already exists.")
        return
    
    # Create and save an empty list
    if save_watchlist(new_name, {}):
        # Update the select box state to the newly created list
        st.session_state.selected_watchlist = new_name 
        st.session_state.new_watchlist_name = ""

def delete_company_from_watchlist_callback(cik_to_delete):
    """Callback to delete a company from the currently selected watchlist."""
    list_name = st.session_state.selected_watchlist
    
    if not list_name or list_name not in st.session_state.watchlists:
        st.error("No watchlist selected or found.")
        return

    current_list = st.session_state.watchlists[list_name]
    if cik_to_delete in current_list:
        company_name = current_list.pop(cik_to_delete)
        st.session_state.watchlists[list_name] = current_list
        
        # Save the updated list back to Firestore
        if save_watchlist(list_name, current_list):
            st.toast(f"Removed {company_name} from '{list_name}'.")
    else:
        st.warning(f"CIK {cik_to_delete} not found in '{list_name}'.")

# --- GENERAL CALLBACK FUNCTIONS ---

def update_target_cik(cik):
    """Callback to set the CIK input and main CIK state for the other tab."""
    st.session_state.cik_input_final = str(cik).zfill(10)
    st.session_state.target_cik = str(cik).zfill(10)
    st.toast(f"CIK {cik} copied to the 'Company Filings & Metrics' tab!")

# --- DATA FETCHING FUNCTIONS ---

@st.cache_data(ttl=86400)
def get_cik_data(ticker, headers):
    """Fetches the CIK and company name using the SEC's EDGAR search."""
    SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    params = {'action': 'getcompany', 'Company': ticker, 'output': 'xml'}

    try:
        sleep(0.5) 
        response = requests.get(SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()

        found_cik = None
        cik_match_1 = re.search(r'/edgar/data/(\d{10})/', response.text)
        
        if cik_match_1:
            found_cik = cik_match_1.group(1)
        
        if found_cik:
            name_match = re.search(r'<title>(.+?) - S', response.text)
            company_name = name_match.group(1) if name_match else f"Ticker Search: {ticker}"
            return found_cik, company_name
        
        return None, None
        
    except requests.exceptions.RequestException as e:
        st.error(f"Error during Ticker Lookup: {e}")
        return None, None

def search_cik_callback(app_name, email):
    """Callback for the CIK Lookup button in the CIK Lookup tab."""
    ticker_input = st.session_state.ticker_input_p
    if not ticker_input:
        st.warning("Please enter a stock ticker.")
        return

    with st.spinner(f"Searching for CIK for {ticker_input}..."):
        # Update HEADERS locally for this call
        local_headers = {'User-Agent': f'{app_name} / {email}', 'Accept-Encoding': 'gzip, deflate', 'Host': 'www.sec.gov'}
        cik, company_name = get_cik_data(ticker_input, local_headers)

        if cik:
            st.success(f"Found CIK for {company_name}: **{cik}**")
            st.session_state.target_cik = cik
            st.session_state.cik_lookup_result = f"CIK: {cik}, Company: {company_name}"
            # This is important: it sets the main CIK input for the other tab
            st.session_state.cik_input_final = cik 
        else:
            st.error(f"Could not find a CIK for ticker: {ticker_input}")
            st.session_state.cik_lookup_result = f"Could not find CIK for {ticker_input}"


@st.cache_data(ttl=3600)
def fetch_sec_company_facts(cik, headers):
    """
    Fetches the full company facts JSON from the SEC for detailed metrics.
    """
    padded_cik = str(cik).zfill(10)
    url = CIK_FACTS_URL.format(cik=padded_cik)
    
    sleep(0.5) # SEC compliance
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching company facts for CIK {padded_cik}: {e}")
        return None

def extract_metric(facts_json, taxonomy, tag, unit='USD'):
    """
    Extracts the latest value and period for a specific financial metric.
    """
    if facts_json is None: return None, None

    try:
        # Navigate the facts JSON structure
        data = facts_json['facts'][taxonomy][tag]['units'][unit]
        
        # Get the most recent non-empty value
        # Data is a list of dicts, we sort by 'end' date to find the latest
        data_sorted = sorted(data, key=lambda x: pd.to_datetime(x.get('end', '1900-01-01'), errors='coerce'), reverse=True)
        
        # Find the first data point that has a 'val'
        for entry in data_sorted:
            if 'val' in entry:
                return entry['val'], entry.get('end', 'N/A')
        
        return None, None # No value found
        
    except KeyError:
        return None, None # Metric not found


def display_key_metrics(facts_json):
    """
    Displays key financial metrics in a readable format.
    """
    if facts_json is None:
        return

    company_name = facts_json.get('entityData', {}).get('name', 'N/A')
    st.subheader(f"Key Metrics for {company_name}")

    metrics_to_fetch = [
        ("Assets (Total)", "us-gaap", "Assets", 'USD'),
        ("Revenues (TTM)", "us-gaap", "Revenues", 'USD'),
        ("Net Income (TTM)", "us-gaap", "NetIncomeLoss", 'USD'),
        ("Cash (Latest)", "us-gaap", "CashAndCashEquivalentsAtCarryingValue", 'USD')
    ]

    metric_data = []
    
    for label, taxonomy, tag, unit in metrics_to_fetch:
        value, date_end = extract_metric(facts_json, taxonomy, tag, unit)
        
        if value is not None:
            formatted_value = f"${value:,.0f}" if value > 1000 else f"${value:,.2f}"
        else:
            formatted_value = "N/A"
        
        metric_data.append({"Metric": label, "Value": formatted_value, "As of": date_end or 'N/A'})

    df_metrics = pd.DataFrame(metric_data)
    
    # Use columns for a card-like view
    cols = st.columns(len(df_metrics))
    for i, row in df_metrics.iterrows():
        cols[i].metric(
            label=row['Metric'], 
            value=row['Value'], 
            delta=f"As of {row['As of']}"
        )


@st.cache_data(ttl=3600)
def fetch_edgar_filings_list(cik, headers):
    """Scrapes the company's EDGAR document list page for recent filings."""
    padded_cik = str(cik).zfill(10)
    EDGAR_URL = f'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded_cik}&type=&dateb=&owner=exclude&count=100'
    
    sleep(0.5) 
    
    try:
        data_headers = headers.copy()
        data_headers['Host'] = 'www.sec.gov' 
        response = requests.get(EDGAR_URL, headers=data_headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        filings_table = soup.find('table', class_='tableFile2')
        if not filings_table: return []

        filings_data = []
        for row in filings_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 5:
                form_type = cols[0].text.strip()
                date_filed = cols[3].text.strip()
                document_link_tag = cols[1].find('a')
                if document_link_tag:
                    relative_href = document_link_tag.get('href')
                    document_link = f"https://www.sec.gov{relative_href}"
                    filings_data.append({
                        'CIK': padded_cik, 
                        'Company Name': '', 
                        'Filing Type': form_type,
                        'Filing Date': date_filed,
                        'Link': document_link,
                        'Description': 'Link to Index File' 
                    })
        
        return filings_data

    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching EDGAR list: {e}")
        return []

def display_company_filings(facts_json, cik, company_name, headers):
    """Displays the recent company filings using the robust scraping method."""
    st.markdown("---")
    st.header(f"Recent Filings ({company_name})")

    with st.spinner(f"Fetching list of recent filings for {company_name}..."):
        st.info("Using the robust EDGAR document search to ensure we capture all recent filings.")
        filings_for_df = fetch_edgar_filings_list(cik, headers)
        
    if not filings_for_df:
        st.warning("No recent filings data available.")
        return

    df = pd.DataFrame(filings_for_df)
    df['Company Name'] = company_name

    all_filing_types = set(df['Filing Type'].unique())
    default_selection = [t for t in ['10-K', '10-Q', '8-K', '4', 'S-1'] if t in all_filing_types]
    if not default_selection and all_filing_types:
        default_selection = sorted(list(all_filing_types))[:3]
    
    selected_types = st.multiselect(
        "Filter Filings by Type:",
        options=sorted(list(all_filing_types)),
        default=default_selection,
        key='single_company_form_filter'
    )

    if not selected_types:
        st.warning("Select one or more filing types to display.")
        return
        
    df_filtered = df[df['Filing Type'].isin(selected_types)]
    df_display = df_filtered.head(20).copy()

    df_final = df_display[['Filing Type', 'Filing Date', 'Link']]
    
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document")
        }
    )
    st.caption(f"Showing {len(df_final)} of the most recent filtered filings for {company_name}.")


# --- Master Index Functions ---

@st.cache_data(ttl=86400)
def fetch_master_index_filings(year, qtr, headers):
    """Fetches and parses a quarterly master index file."""
    MASTER_INDEX_URL = f'https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/master.idx'
    st.info(f"Fetching SEC Master Index for {year} Q{qtr}. This file contains thousands of filings and may take a moment to load.")
    sleep(0.5)
    
    try:
        data_headers = headers.copy()
        data_headers['Host'] = 'www.sec.gov' 
        response = requests.get(MASTER_INDEX_URL, headers=data_headers)
        response.raise_for_status() 
        content = response.text
        
        if len(content.splitlines()) < 12:
            st.warning("The fetched index file appears to be empty or incomplete. Try a different quarter.")
            return pd.DataFrame()

        content_lines = content.splitlines()
        data_lines = content_lines[11:] 
        
        data = StringIO("\n".join(data_lines))
        df = pd.read_csv(data, sep='|', header=None, 
                         names=['CIK', 'Company Name', 'Form Type', 'Date Filed', 'Filename'])
        
        df['CIK'] = df['CIK'].astype(str).str.zfill(10)
        df['Link'] = df['Filename'].apply(lambda x: f"https://www.sec.gov/Archives/{x}")

        return df
        
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching Master Index for {year} Q{qtr}: {e}")
        return pd.DataFrame()

def load_master_index_callback(selected_key, index_options):
    """Callback to load and store the master index file."""
    if st.session_state.loaded_index_key == selected_key:
        st.info("Index already loaded.")
        return

    year, qtr = index_options[selected_key]
    
    with st.spinner(f"Loading Master Index for {selected_key}..."):
        df = fetch_master_index_filings(year, qtr, HEADERS)
        
        if not df.empty:
            st.session_state.master_filings_df = df
            st.session_state.loaded_index_key = selected_key
            st.success(f"Successfully loaded {len(df):,} filings for {selected_key}.")
        else:
            st.session_state.master_filings_df = pd.DataFrame()
            st.session_state.loaded_index_key = ""
            st.error("Failed to load filings.")

def clear_master_index_callback():
    """Callback to clear the loaded master index data."""
    st.session_state.master_filings_df = pd.DataFrame()
    st.session_state.loaded_index_key = ""
    st.toast("Master index data cleared.")

# --- Watchlist Summary Display Function ---

def display_watchlist_summary(watchlist_companies, headers):
    """
    Fetches and merges the recent filings for all companies in the selected watchlist.
    """
    if not watchlist_companies:
        st.warning("This watchlist is empty. Add companies using the sidebar controls.")
        return

    st.header(f"Combined Recent Filings for Watchlist: {st.session_state.selected_watchlist}")
    
    all_filings = []
    
    # We use st.cache_data here to cache the combined result for a period
    @st.cache_data(ttl=600)
    def fetch_combined_watchlist_filings(_companies, _headers):
        combined_filings = []
        for cik, name in _companies.items():
            st.caption(f"Fetching filings for {name} ({cik})...")
            filings = fetch_edgar_filings_list(cik, _headers)
            for filing in filings:
                filing['Company Name'] = name
                combined_filings.append(filing)
        return combined_filings

    with st.spinner("Fetching and combining recent filings from EDGAR for all companies..."):
        all_filings = fetch_combined_watchlist_filings(watchlist_companies, headers)
        
    if not all_filings:
        st.error("Could not retrieve any filings for the companies in this watchlist.")
        return

    df_raw = pd.DataFrame(all_filings)
    
    # Process date column
    df_raw['Filing Date'] = pd.to_datetime(df_raw['Filing Date'], errors='coerce')
    df_raw.dropna(subset=['Filing Date'], inplace=True)
         
    st.markdown("---")
    
    filter_cols = st.columns([1, 2])
    
    # 1. Company Filter
    all_company_names = sorted(df_raw['Company Name'].unique())
    selected_companies = filter_cols[0].multiselect(
        "Filter by Company:",
        options=all_company_names,
        default=all_company_names,
        key='watchlist_company_filter'
    )
    
    # 2. Filing Type Filter
    all_forms = sorted(df_raw['Filing Type'].unique())
    default_forms = [f for f in ['10-K', '10-Q', '8-K', '4', 'S-1', 'D'] if f in all_forms]
    selected_forms = filter_cols[1].multiselect(
        "Filter Filings by Type:",
        options=all_forms,
        default=default_forms,
        key='watchlist_form_filter'
    )
    
    df_filtered = df_raw[
        df_raw['Company Name'].isin(selected_companies) & 
        df_raw['Filing Type'].isin(selected_forms)
    ]

    # --- Final Display ---
    df_display = df_filtered.sort_values(by='Filing Date', ascending=False).head(500)
    
    st.subheader(f"Filtered Filings ({len(df_display):,} Found)")
    
    # Add a 'Use CIK' button column
    df_display['Action'] = df_display['CIK'].apply(lambda x: f"CIK: {x}")
    
    st.dataframe(
        df_display[['Company Name', 'Filing Type', 'Filing Date', 'Link', 'Action']],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document"),
            "Action": st.column_config.ButtonColumn(
                "Use CIK", 
                help="Click to set this CIK in the 'Company Filings & Metrics' tab.", 
                on_click=update_target_cik, 
                args=['CIK']
            )
        },
        column_order=['Company Name', 'Filing Type', 'Filing Date', 'Link', 'Action']
    )


# --- Streamlit Main App Layout ---

def main():
    st.set_page_config(
        page_title="SEC EDGAR Data Viewer",
        layout="wide", 
        initial_sidebar_state="expanded"
    )
    
    st.title("SEC EDGAR Data Viewer")
    
    # --- Initialize Firebase and Load Watchlists ---
    firebase_ready = initialize_firebase()
    if firebase_ready:
        # Load watchlists only if initialization was successful
        load_watchlists() 

    # --- Sidebar Setup ---
    
    # --- 1. SEC Compliance ---
    st.sidebar.header("SEC Compliance (Required)")
    if 'app_name' not in st.session_state: st.session_state.app_name = "Financial-Data-App"
    if 'email' not in st.session_state: st.session_state.email = "user@example.com"
        
    app_name = st.sidebar.text_input("Application Name:", key='app_name')
    email = st.sidebar.text_input("Contact Email:", key='email')
    
    HEADERS['User-Agent'] = f'{app_name} / {email}'
    
    # --- 2. Watchlist Management ---
    st.sidebar.markdown("---")
    st.sidebar.header("Personal Watchlists")

    if not firebase_ready:
        st.sidebar.error("Database unavailable. Watchlists cannot be managed.")
    else:
        
        watchlist_names = list(st.session_state.watchlists.keys())
        # Add a placeholder if no lists exist
        options_list = watchlist_names if watchlist_names else ["<No Watchlists>"]
        
        # Determine the default index for the select box
        default_index = 0
        if st.session_state.selected_watchlist in watchlist_names:
            default_index = watchlist_names.index(st.session_state.selected_watchlist)
        
        # Select Watchlist
        selected_list_name = st.sidebar.selectbox(
            "Select Watchlist:",
            options=options_list,
            index=default_index,
            key='watchlist_select_box'
        )
        st.session_state.selected_watchlist = selected_list_name # Update main state
        
        # Display current companies in the selected watchlist
        if selected_list_name and selected_list_name != "<No Watchlists>":
            current_list = st.session_state.watchlists.get(selected_list_name, {})
            st.sidebar.caption(f"**{len(current_list)}** companies in list.")
            
            with st.sidebar.expander("Current Companies"):
                if current_list:
                    companies_df = pd.DataFrame(
                        [(cik, name) for cik, name in current_list.items()], 
                        columns=['CIK', 'Name']
                    ).sort_values(by='Name')
                    
                    for index, row in companies_df.iterrows():
                        col_name, col_delete = st.columns([3, 1])
                        col_name.text(f"{row['Name']} ({row['CIK'][-4:]})")
                        col_delete.button(
                            "🗑️", 
                            key=f"delete_{row['CIK']}", 
                            help="Remove from watchlist",
                            on_click=delete_company_from_watchlist_callback,
                            args=(row['CIK'],)
                        )
                else:
                    st.write("List is currently empty.")


        st.sidebar.markdown("##### Add New Company to Selected List")
        st.sidebar.text_input("Company Name (optional):", key='company_name_input')
        st.sidebar.text_input("CIK (e.g., 320193):", key='company_cik_input', max_chars=10)
        
        st.sidebar.button(
            f"Add to '{selected_list_name}'" if selected_list_name and selected_list_name != "<No Watchlists>" else "Add Company",
            on_click=add_company_to_watchlist_callback,
            key='add_company_button',
            disabled=(selected_list_name is None or selected_list_name == "<No Watchlists>")
        )
        
        st.sidebar.markdown("##### Create New Watchlist")
        st.sidebar.text_input("New Watchlist Name:", key='new_watchlist_name')
        st.sidebar.button(
            "Create List",
            on_click=create_watchlist_callback,
            key='create_watchlist_button'
        )

    st.sidebar.markdown("---")

    # --- TAB STRUCTURE ---
    tab_watchlist, tab_data, tab_daily_index, tab_lookup = st.tabs([
        "Watchlist Summary",
        "Company Filings & Metrics", 
        "Daily Filings Index",
        "CIK Lookup (Experimental)"
    ])
    
    # --- 1. Watchlist Summary Tab ---
    with tab_watchlist:
        if not firebase_ready:
            st.error("Watchlist features require a functioning database connection.")
        elif st.session_state.selected_watchlist and st.session_state.selected_watchlist != "<No Watchlists>":
            companies = st.session_state.watchlists.get(st.session_state.selected_watchlist, {})
            display_watchlist_summary(companies, HEADERS)
        else:
            st.info("Select or create a watchlist in the sidebar to view a summary of all included companies' filings.")


    # --- 2. Company Filings & Metrics Tab (Main Working Feature) ---
    with tab_data:
        st.header("Fetch Data by CIK")
        st.markdown("Use this section to fetch detailed financial metrics and recent filings for a **single company** identified by its Central Index Key (CIK).")
        
        # CIK Input Section
        target_cik = st.text_input(
            "Central Index Key (CIK):", 
            value=st.session_state.target_cik, 
            max_chars=10,
            placeholder="e.g., 320193",
            key='cik_input_final'
        ).strip()
        
        if st.button("Fetch Financials & Filings", key='fetch_data_button'):
            
            if not target_cik.isdigit():
                st.error("Please enter a valid numeric CIK to fetch data.")
                return

            if app_name.strip() == "Financial-Data-App" or email.strip() == "user@example.com":
                 st.warning("Please update the Application Name and Contact Email in the sidebar for SEC compliance.")

            # 1. Fetch Company Facts (for Metrics)
            with st.spinner(f"Fetching company facts for CIK {target_cik}..."):
                facts_json = fetch_sec_company_facts(target_cik, HEADERS)

            if facts_json:
                company_name = facts_json.get('entityData', {}).get('name', f"CIK {target_cik}")
                display_key_metrics(facts_json)
                
                # 2. Fetch and Display Filings
                display_company_filings(facts_json, target_cik, company_name, HEADERS)
            else:
                st.error(f"Failed to retrieve financial facts for CIK {target_cik}. Please verify the CIK is correct.")


    # --- 3. Daily Filings Index Tab (Non-Company Specific Feature) ---
    with tab_daily_index:
        st.header("Browse Recent SEC Filings Index")
        
        # Define recent index options (relative to today's date for freshness)
        current_date = date.today()
        current_year = current_date.year
        current_qtr = (current_date.month - 1) // 3 + 1
        
        index_options = {}
        for i in range(4): # Show last 4 quarters
            qtr_end_date = current_date - relativedelta(months=3 * i)
            year = qtr_end_date.year
            qtr = (qtr_end_date.month - 1) // 3 + 1
            start_month = ['Jan', 'Apr', 'Jul', 'Oct'][qtr - 1]
            end_month = ['Mar', 'Jun', 'Sep', 'Dec'][qtr - 1]
            key = f"{year} Q{qtr} ({start_month} - {end_month})"
            index_options[key] = (year, qtr)

        selected_key = st.selectbox(
            "Choose Master Index Quarter:", 
            options=list(index_options.keys()), 
            index=0, 
            key='index_quarter_select'
        )
        
        col1, col2 = st.columns([1, 1])
        col1.button("Load Filings", on_click=load_master_index_callback, args=(selected_key, index_options))
        col2.button("Clear Loaded Data", on_click=clear_master_index_callback)
        
        df = st.session_state.master_filings_df
        if not df.empty:
            st.subheader(f"Filings for {st.session_state.loaded_index_key} ({len(df):,} Total)")
            
            # Filtering
            unique_forms = sorted(df['Form Type'].unique())
            form_filter = st.multiselect(
                "Filter by Form Type:", 
                options=unique_forms, 
                default=[f for f in ['10-K', '10-Q', '8-K'] if f in unique_forms],
                key='master_index_form_filter'
            )
            
            df_filtered = df[df['Form Type'].isin(form_filter)]
            
            st.caption(f"Showing {len(df_filtered):,} filtered filings.")

            # Create a button column for CIK usage
            df_display = df_filtered.head(50).copy()
            df_display['Action'] = df_display['CIK'].apply(lambda x: f"CIK: {x}")
            
            st.dataframe(
                df_display[['Company Name', 'Form Type', 'Date Filed', 'Link', 'Action']],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document"),
                    "Action": st.column_config.ButtonColumn(
                        "Use CIK", 
                        help="Click to set this CIK in the 'Company Filings & Metrics' tab.", 
                        on_click=update_target_cik, 
                        args=['CIK']
                    )
                },
                column_order=['Company Name', 'Form Type', 'Date Filed', 'Link', 'Action']
            )

    # --- 4. CIK Lookup (Experimental) Tab ---
    with tab_lookup:
        st.header("Search CIK by Ticker (Experimental)")
        st.caption("This uses the SEC's search engine and can sometimes be unreliable or slow.")
        
        ticker_input = st.text_input("Enter Stock Ticker:", value="", key='ticker_input_p').strip().upper()
        
        st.button(
            "Search CIK", 
            on_click=search_cik_callback, 
            args=(app_name, email)
        )
        
        if 'cik_lookup_result' in st.session_state and st.session_state.cik_lookup_result:
            st.markdown(f"**Result:** {st.session_state.cik_lookup_result}")

if __name__ == '__main4__':
    main()
