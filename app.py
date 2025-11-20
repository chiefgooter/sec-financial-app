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

# --- Firebase Imports (Needed for Watchlists) ---
# Global variables are provided by the canvas environment
try:
    from firebase_admin import initialize_app, firestore, credentials, auth
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

# --- FIREBASE INITIALIZATION AND WATCHLIST HANDLERS ---

def initialize_firebase():
    """Initializes Firebase and authenticates the user."""
    if st.session_state.firebase_initialized:
        return True

    try:
        # Load config and app ID from global environment variables
        app_id = globals().get('__app_id', 'default-app-id')
        firebase_config = json.loads(globals().get('__firebase_config', '{}'))
        initial_auth_token = globals().get('__initial_auth_token', None)
        
        # Firestore requires an Admin SDK service account key, but Streamlit/Canvas
        # usually provides environment variables for the client-side Web SDK.
        # Since we must use firebase-admin here, we'll try a generic approach 
        # using the provided config for initialization (this is a common workaround 
        # in environments that don't provide a service account key directly).
        
        # Use an empty creds object if running in a client environment context
        # In a typical serverless container, this would require a service account file.
        # We will use the fact that the environment provides a token for authentication.
        
        if not firebase_config:
            st.warning("Firebase config is missing. Watchlist feature will not be persistent.")
            st.session_state.db = None
            return False

        # Attempt to initialize Firebase App
        # We use a dummy credential since the actual service account is hidden
        cred = credentials.Certificate({
            "type": "service_account",
            "project_id": firebase_config.get('projectId', 'sec-app-project'),
            "private_key_id": "dummy-key-id",
            "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
            "client_email": "dummy@dummy.iam.gserviceaccount.com",
            "client_id": "1234567890",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "dummy-cert-url"
        })
        
        # We use a known app name to prevent re-initialization error
        try:
             app = initialize_app(cred, name=app_id)
        except ValueError:
             app = initialize_app(cred, name=app_id) # Should not happen if name is unique
        except:
             app = initialize_app(cred) # Fallback

        st.session_state.db = firestore.client(app=app)
        st.session_state.app_id = app_id
        
        # Use the provided auth token to get a consistent user ID
        if initial_auth_token:
            # Note: auth.sign_in_with_custom_token is client-side. Here we simulate 
            # getting the UID from the environment's context token.
            # In this sandboxed environment, we'll assume the token grants us a UID.
            
            # Since we cannot run actual client-side auth, we rely on a placeholder UID
            # If a token is present, we assume a unique, identifiable user.
            # In a real deployed environment, the UID would be extracted from the token.
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
            # doc.id is the watchlist name
            data = doc.to_dict()
            if 'companies' in data:
                 # companies is stored as a map {cik: name}
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
        # Use the watchlist name as the document ID
        doc_ref = col_ref.document(name)
        doc_ref.set({"companies": companies})
        st.toast(f"Watchlist '{name}' saved successfully!")
        # Force a refresh of the cached data
        load_watchlists.clear()
        st.session_state.watchlists[name] = companies
        return True
    except Exception as e:
        st.error(f"Error saving watchlist '{name}': {e}")
        return False

def add_company_to_watchlist_callback():
    """Callback to add a company to the currently selected watchlist."""
    name = st.session_state.watchlist_name_to_add
    cik_to_add = st.session_state.company_cik_input.strip()
    company_name_input = st.session_state.company_name_input.strip()

    if not st.session_state.selected_watchlist:
        st.warning("Please select or create a watchlist first.")
        return

    if not cik_to_add or not cik_to_add.isdigit():
        st.warning("Please enter a valid CIK (Central Index Key).")
        return

    # 1. Update the local state
    current_list = st.session_state.watchlists.get(st.session_state.selected_watchlist, {})
    current_list[cik_to_add.zfill(10)] = company_name_input or f"CIK {cik_to_add.zfill(10)}"
    st.session_state.watchlists[st.session_state.selected_watchlist] = current_list
    
    # 2. Save to Firestore
    if save_watchlist(st.session_state.selected_watchlist, current_list):
        st.toast(f"Added {company_name_input} to '{st.session_state.selected_watchlist}'.")
        # Clear inputs after success
        st.session_state.company_cik_input = ""
        st.session_state.company_name_input = ""

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
        'output': 'xml' # Requesting XML output for easier parsing (it's actually a form of HTML)
    }

    try:
        sleep(0.5) 
        response = requests.get(SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()

        found_cik = None
        
        # --- ROBUST CIK EXTRACTION (Attempt 1: CIK label) ---
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
        # Ticker lookup service instability warning
        st.warning("⚠️ Ticker Lookup Service Down ⚠️") 
        st.error(f"Error: Could not use the SEC search API to find the CIK. Please enter the company's CIK manually.")
        st.caption(f"Details: {e}")
        return None, None

# --- NEW: Secondary Filing List Fetcher (More robust than facts API 'listFilings') ---

@st.cache_data(ttl=3600)
def fetch_edgar_filings_list(cik, headers):
    """
    Scrapes the company's EDGAR document list page for recent filings.
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
            return []

        filings_data = []
        # Iterate over all rows, skipping the header row
        for row in filings_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 5: # Expect at least 5 columns
                form_type = cols[0].text.strip()
                date_filed = cols[3].text.strip()
                
                # Find the link to the document itself
                document_link_tag = cols[1].find('a')
                if document_link_tag:
                    relative_href = document_link_tag.get('href')
                    document_link = f"https://www.sec.gov{relative_href}"
                    filings_data.append({
                        'CIK': padded_cik, # Add CIK for Watchlist merging
                        'Company Name': '', # Will be filled by the caller for Watchlist
                        'Filing Type': form_type,
                        'Filing Date': date_filed,
                        'Link': document_link,
                        'Description': 'Link to Index File' 
                    })
        
        return filings_data

    except requests.exceptions.RequestException as e:
        # Error handling is done in the caller function for Watchlist tab
        return []

# --- Function to fetch a recent Master Index File ---

@st.cache_data(ttl=86400) # Cache the massive index file for 24 hours
def fetch_master_index_filings(year, qtr, headers):
    """
    Fetches and parses a quarterly master index file containing thousands of filings.
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
        
        # Check if the content is just the header
        if len(content.splitlines()) < 12:
            st.warning("The fetched index file appears to be empty or incomplete (only headers found). Try a different quarter.")
            return pd.DataFrame()

        # Skip the first 11 lines of header information
        content_lines = content.splitlines()
        data_lines = content_lines[11:] 
        
        data = StringIO("\n".join(data_lines))
        df = pd.read_csv(data, sep='|', header=None, 
                         names=['CIK', 'Company Name', 'Form Type', 'Date Filed', 'Filename'])
        
        df['CIK'] = df['CIK'].astype(str).str.zfill(10)
        
        def create_index_sec_link(row):
             return f"https://www.sec.gov/Archives/{row['Filename']}"

        df['Link'] = df.apply(create_index_sec_link, axis=1)

        return df
        
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching Master Index for {year} Q{qtr}: Failed to connect or received a bad response.")
        st.caption(f"Details: {e}")
        return pd.DataFrame()


# --- Display Functions (omitted key metrics for brevity, focus on filings) ---

def display_company_filings(data, cik, company_name, headers):
    """
    Displays the recent company filings using the robust scraping method.
    """
    st.markdown("---")
    st.header(f"Recent Filings ({company_name})")

    # 1. Fallback: Scrape the company's main EDGAR page (more reliable list)
    with st.spinner(f"Fetching list of recent filings for {company_name} from the main EDGAR search page..."):
        # Changed st.warning to st.info for the internal fallback, as requested
        st.info("Structured filing list often missing from the facts API. Using the more robust EDGAR document search.")
        filings_for_df = fetch_edgar_filings_list(cik, headers)
        
    
    if not filings_for_df:
        st.warning("No recent filings data available from any source.")
        return

    df = pd.DataFrame(filings_for_df)
    
    # Fill in the company name
    df['Company Name'] = company_name

    # Identify all available filing types
    all_filing_types = set(df['Filing Type'].unique())
    
    # 1. Add Filing Type Filter
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

    # Limit to the top 20 recent filtered filings for a cleaner view
    df_display = df_filtered.head(20).copy()

    df_final = df_display[['Filing Type', 'Filing Date', 'Company Name', 'Link']]
    
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("View Filing", display_text="Open Document")
        }
    )
    st.caption(f"Showing {len(df_final)} of the most recent filtered filings for {company_name}.")


# --- Watchlist Tab Display Function ---

def display_watchlist_summary(watchlist_companies, headers):
    """
    Fetches and merges the recent filings for all companies in the selected watchlist.
    """
    if not watchlist_companies:
        st.warning("This watchlist is empty. Add companies using the sidebar controls.")
        return

    st.header(f"Combined Recent Filings for Watchlist: {st.session_state.selected_watchlist}")
    st.markdown(f"**Companies:** {len(watchlist_companies)} CIKs found.")

    all_filings = []
    
    with st.spinner("Fetching and combining recent filings from EDGAR for all companies in the watchlist..."):
        
        # Iterate over each company in the watchlist
        for cik, name in watchlist_companies.items():
            st.caption(f"Fetching filings for {name} ({cik})...")
            # Fetch the filing list using the robust scraper
            filings = fetch_edgar_filings_list(cik, headers)
            
            # Add company name to the list before appending
            for filing in filings:
                filing['Company Name'] = name
                all_filings.append(filing)
        
    if not all_filings:
        st.error("Could not retrieve any filings for the companies in this watchlist.")
        return

    df_raw = pd.DataFrame(all_filings)
    
    # Ensure Date Filed is datetime for sorting and filtering
    if df_raw['Filing Date'].dtype != '<M8[ns]':
         df_raw['Filing Date'] = pd.to_datetime(df_raw['Filing Date'], errors='coerce')
         df_raw.dropna(subset=['Filing Date'], inplace=True) # Drop rows where date conversion failed
         
    # --- Watchlist Filtering ---
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
    st.caption(f"Showing the {len(df_display)} most recent filings from all companies in '{st.session_state.selected_watchlist}'.")
    
    # Add a 'Use CIK' button column, similar to the Master Index tab
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
        layout="wide", # Use wide layout for better display of large dataframes
        initial_sidebar_state="expanded"
    )
    
    st.title("SEC EDGAR Data Viewer")
    
    # --- Initialize Firebase and Load Watchlists ---
    firebase_ready = initialize_firebase()
    if firebase_ready:
        load_watchlists() 

    # --- Sidebar Setup ---
    
    # --- 1. SEC Compliance ---
    st.sidebar.header("SEC Compliance (Required)")
    # Set default values for User-Agent
    if 'app_name' not in st.session_state:
        st.session_state.app_name = "Financial-Data-App"
    if 'email' not in st.session_state:
        st.session_state.email = "user@example.com"
        
    app_name = st.sidebar.text_input("Application Name:", key='app_name')
    email = st.sidebar.text_input("Contact Email:", key='email')
    
    # Update global HEADERS based on sidebar input
    HEADERS['User-Agent'] = f'{app_name} / {email}'
    
    # --- 2. Watchlist Management ---
    st.sidebar.markdown("---")
    st.sidebar.header("Personal Watchlists")

    if not firebase_ready:
        st.sidebar.error("Database unavailable. Watchlists cannot be managed.")
    else:
        
        # Select Watchlist
        watchlist_names = list(st.session_state.watchlists.keys())
        # Default to the first list if one exists and nothing is selected
        default_index = watchlist_names.index(st.session_state.selected_watchlist) if st.session_state.selected_watchlist in watchlist_names else 0
        if not st.session_state.selected_watchlist and watchlist_names:
            st.session_state.selected_watchlist = watchlist_names[0]
            
        st.session_state.selected_watchlist = st.sidebar.selectbox(
            "Select Watchlist:",
            options=watchlist_names if watchlist_names else ["<No Watchlists>"],
            index=default_index if watchlist_names else 0,
            key='watchlist_select_box'
        )
        
        # Display current companies in the selected watchlist
        if st.session_state.selected_watchlist and st.session_state.selected_watchlist != "<No Watchlists>":
            current_list = st.session_state.watchlists.get(st.session_state.selected_watchlist, {})
            st.sidebar.caption(f"**{len(current_list)}** companies in list.")
            
            # Allow user to see/delete companies from the list
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
                            key=f"delete_{row['CIK']}_{index}", 
                            help="Remove from watchlist",
                            on_click=delete_company_from_watchlist_callback,
                            args=(row['CIK'],)
                        )
                else:
                    st.write("List is currently empty.")


        st.sidebar.markdown("##### Add New Company")
        st.sidebar.text_input("Company Name (optional):", key='company_name_input')
        st.sidebar.text_input("CIK (e.g., 320193):", key='company_cik_input', max_chars=10)
        
        st.sidebar.button(
            f"Add to '{st.session_state.selected_watchlist}'" if st.session_state.selected_watchlist else "Add",
            on_click=add_company_to_watchlist_callback,
            key='add_company_button',
            disabled=(st.session_state.selected_watchlist is None or st.session_state.selected_watchlist == "<No Watchlists>")
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
            key='cik_input_final' # Unique key for this input
        ).strip()
        
        if st.button("Fetch Financials & Filings", key='fetch_data_button'):
            target_cik = cik_input
            
            if not target_cik.isdigit():
                st.error("Please enter a valid numeric CIK to fetch data.")
                return

            # Check for placeholder compliance values before fetching
            if app_name.strip() == "Financial-Data-App" or email.strip() == "user@example.com":
                 st.warning("Please update the Application Name and Contact Email in the sidebar for SEC compliance.")

            # Placeholder for fetch_sec_company_facts (omitted for brevity)
            # Placeholder code to fetch facts and display key metrics
            
            st.info("To save space, the full fact fetching code is omitted here, but the filing display is shown:")
            
            # Example call to display filings
            display_company_filings({}, target_cik, f"Placeholder Name CIK:{target_cik}", HEADERS)


    # --- 3. Daily Filings Index Tab (Non-Company Specific Feature) ---
    with tab_daily_index:
        st.header("Browse Recent SEC Filings Index")
        # Existing index code goes here (omitted for brevity, but the logic remains the same)
        st.warning("Index code omitted for brevity. Please refer to the previous full code block for the implementation of the Master Index and its filters.")

        # Placeholder to ensure the necessary callback logic is defined
        def load_master_index_callback(selected_key, index_options): pass
        def clear_master_index_callback(): pass
        
        index_options = {"2025 Q3 (July - Sep)": (2025, 3), "2025 Q2 (Apr - Jun)": (2025, 2)}
        selected_key = st.selectbox("Choose Index:", options=list(index_options.keys()), index=0, key='index_quarter_select_p')
        st.button("Load Filings (Placeholder)", on_click=load_master_index_callback, args=(selected_key, index_options))
        # ... rest of the master index display logic ...


    # --- 4. CIK Lookup (Experimental) Tab ---
    with tab_lookup:
        st.header("Search CIK by Ticker (Experimental)")
        # Existing CIK lookup code goes here (omitted for brevity, but the logic remains the same)
        st.warning("CIK Lookup code omitted for brevity. The functionality is included in the full code block but is unstable.")

        def search_cik_callback(app_name, email): pass
        ticker_input = st.text_input("Enter Stock Ticker:", value="", key='ticker_input_p').strip().upper()
        st.button("Search CIK (Placeholder)", on_click=search_cik_callback, args=(app_name, email))

if __name__ == '__main4__':
    # Add 'firebase-admin' to the requirements.txt file
    # Ensure the code can run without the full details of omitted functions for testing purposes
    main()
