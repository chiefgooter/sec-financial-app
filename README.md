# SEC Financial Data Streamlit App

This is a simple web application built using Streamlit and Python to demonstrate how to access structured financial data directly from the U.S. Securities and Exchange Commission (SEC) EDGAR API.

The app retrieves key financial metrics (like **Revenues** and **Total Assets**) for publicly traded companies using their Central Index Key (CIK).

## Prerequisites

To run this application, you need to have Python 3.8+ installed, along with the required libraries.

## Installation and Setup

1.  **Clone the Repository:**

    ```bash
    git clone [https://github.com/YourUsername/sec-financial-app.git](https://github.com/YourUsername/sec-financial-app.git)
    cd sec-financial-app
    ```

2.  **Create a Virtual Environment (Recommended):**

    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use: .\venv\Scripts\activate
    ```

3.  **Install Dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

## Running the App

Execute the Streamlit command from the project root directory:

```bash
streamlit run app.py
