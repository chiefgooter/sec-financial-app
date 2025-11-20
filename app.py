<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Integrated Financial Dashboard</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        /* Custom styles for appearance, leveraging Tailwind utilities */
        body {
            font-family: 'Inter', sans-serif;
        }
        /* Custom styles to make the scrollbar slightly less harsh (necessary for accessibility in dark mode) */
        .custom-scrollbar::-webkit-scrollbar {
            width: 8px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
            background: #1f2937; /* gray-800 */
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
            background: #4b5563; /* gray-600 */
            border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
            background: #6b7280; /* gray-500 */
        }
        /* Style for the analysis content (prose equivalent) */
        .analysis-content h1, .analysis-content h2, .analysis-content h3 {
            font-weight: bold;
            margin-top: 1.5rem;
            margin-bottom: 0.5rem;
            color: #fcd34d; /* yellow-300 */
        }
        .analysis-content ul {
            list-style: disc;
            margin-left: 1.5rem;
            padding-left: 0;
        }
        .analysis-content li {
            margin-bottom: 0.5rem;
        }
        .analysis-content p {
            margin-bottom: 1rem;
        }
    </style>
</head>
<body class="bg-gray-900 text-white antialiased">

    <div id="app" class="flex h-screen">
        <!-- Sidebar (Controlled by JS) -->
        <div id="sidebar" class="fixed inset-y-0 left-0 transform -translate-x-full w-0 transition-all duration-300 ease-in-out z-30 
                             bg-gray-800 border-r border-gray-700 shadow-xl md:relative md:flex-shrink-0 md:translate-x-0 md:w-64">
            
            <div class="p-4 flex flex-col h-full">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold text-indigo-400">Financial Tools</h2>
                    <button id="sidebar-close-btn" class="text-gray-400 md:hidden p-1 rounded-full hover:bg-gray-700">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                    </button>
                </div>

                <nav id="nav-items" class="space-y-2 flex-grow">
                    <!-- Nav Items will be dynamically populated/controlled -->
                </nav>

                <div class="mt-4 pt-4 border-t border-gray-700">
                     <p class="text-sm font-mono break-all text-gray-500">
                         <span class="font-bold text-gray-400 mr-1">User ID:</span> <span id="user-id-display">N/A</span>
                     </p>
                </div>
            </div>
        </div>

        <!-- Main Content Area -->
        <div class="flex-1 flex flex-col overflow-y-auto">
            <!-- Mobile Header for Toggle -->
            <header id="mobile-header" class="bg-gray-800 p-4 md:hidden flex justify-between items-center border-b border-gray-700 z-20">
                <button id="sidebar-open-btn" class="p-1 rounded-full hover:bg-gray-700">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" x2="21" y1="12" y2="12"/><line x1="3" x2="21" y1="6" y2="6"/><line x1="3" x2="21" y1="18" y2="18"/></svg>
                </button>
                <h1 id="current-tab-title" class="text-xl font-bold text-indigo-400">Dashboard</h1>
            </header>

            <!-- Message Box (Global) -->
            <div id="message-box" class="p-3 mx-auto mt-4 max-w-4xl rounded-lg shadow-md border z-10 hidden cursor-pointer" onclick="document.getElementById('message-box').classList.add('hidden')">
                <p id="message-text" class="font-medium text-center"></p>
            </div>
            
            <!-- Content -->
            <main id="content-area" class="flex-1 overflow-y-auto"></main>
        </div>
    </div>

    <script type="module">
        import { initializeApp } from "https://www.gstatic.com/firebasejs/11.6.1/firebase-app.js";
        import { getAuth, signInAnonymously, signInWithCustomToken, onAuthStateChanged } from "https://www.gstatic.com/firebasejs/11.6.1/firebase-auth.js";
        import { getFirestore, collection, query, where, onSnapshot, addDoc, deleteDoc, doc, serverTimestamp } from "https://www.gstatic.com/firebasejs/11.6.1/firebase-firestore.js";

        // --- GLOBAL CONFIGURATION AND STATE ---
        const FLASH_MODEL_NAME = "gemini-2.5-flash-preview-09-2025";
        
        const appId = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id';
        const firebaseConfig = typeof __firebase_config !== 'undefined' ? JSON.parse(__firebase_config) : null;
        const initialAuthToken = typeof __initial_auth_token !== 'undefined' ? __initial_auth_token : null;
        
        let firebaseApp, db, auth;
        let userId = null;
        let isAuthReady = false;
        let activeTab = 'filings';
        
        // --- UI UTILITIES ---

        function showMessage(type, text) {
            const box = document.getElementById('message-box');
            const textElement = document.getElementById('message-text');
            box.classList.remove('hidden', 'bg-red-800', 'border-red-600', 'bg-green-800', 'border-green-600', 'bg-blue-800', 'border-blue-600');
            
            let colorClasses = '';
            if (type === 'error') colorClasses = 'bg-red-800 border-red-600';
            else if (type === 'success') colorClasses = 'bg-green-800 border-green-600';
            else colorClasses = 'bg-blue-800 border-blue-600';

            box.classList.add(colorClasses);
            textElement.textContent = text;

            setTimeout(() => {
                box.classList.add('hidden');
            }, 5000);
        }

        // --- API UTILITIES ---

        const withExponentialBackoff = async (fn, retries = 3, delay = 1000) => {
            for (let i = 0; i < retries; i++) {
                try {
                    return await fn();
                } catch (error) {
                    if (i === retries - 1) throw error; 
                    await new Promise(resolve => setTimeout(resolve, delay));
                    delay *= 2;
                }
            }
        };
        
        function safeJsonParse(text) {
            if (!text) return null;
            let cleanedText = text.trim();
            if (cleanedText.startsWith('```')) {
                const lines = cleanedText.split('\n');
                if (lines.length > 1 && (lines[0].startsWith('```json') || lines[0] === '```')) {
                    lines.shift(); 
                    if (lines[lines.length - 1] === '```') {
                        lines.pop(); 
                    }
                }
                cleanedText = lines.join('\n').trim();
            }
            try {
                return JSON.parse(cleanedText);
            } catch (e) {
                console.error("Failed to parse JSON after cleanup:", e, "Original text:", text);
                return null; 
            }
        }
        
        function useDebounce(func, delay) {
            let timeout;
            return function(...args) {
                clearTimeout(timeout);
                timeout = setTimeout(() => func.apply(this, args), delay);
            };
        }

        // --- FIREBASE INITIALIZATION ---

        function initFirebase() {
            if (!firebaseConfig) {
                showMessage('error', 'Firebase configuration is missing.');
                return;
            }

            try {
                firebaseApp = initializeApp(firebaseConfig);
                db = getFirestore(firebaseApp);
                auth = getAuth(firebaseApp);

                const signInUser = async () => {
                    try {
                        if (initialAuthToken) {
                            await signInWithCustomToken(auth, initialAuthToken);
                        } else {
                            await signInAnonymously(auth);
                        }
                    } catch (error) {
                        console.error("Firebase Auth Error:", error);
                        showMessage('error', `Authentication failed: ${error.message}`);
                    }
                };

                onAuthStateChanged(auth, (user) => {
                    if (user) {
                        userId = user.uid;
                        document.getElementById('user-id-display').textContent = userId.substring(0, 8) + '...';
                    } else {
                        signInUser();
                    }
                    isAuthReady = true;
                    // Re-render the current tab to start fetching/listening
                    renderTab(activeTab); 
                });

            } catch (error) {
                console.error("Firebase Initialization Error:", error);
                showMessage('error', `Firebase init failed: ${error.message}`);
            }
        }

        // --- 1. SEC FILINGS TAB LOGIC ---

        async function fetchSecFilings(ticker) {
            const resultsDiv = document.getElementById('filings-results');
            const button = document.getElementById('analyze-btn');
            
            if (!ticker) {
                showMessage('error', 'Please enter a ticker symbol to search.');
                return;
            }

            resultsDiv.innerHTML = '<div class="text-center py-12 text-yellow-300 font-medium text-lg">Analyzing filings... please wait.</div>';
            button.disabled = true;
            button.textContent = 'Analyzing...';

            const systemPrompt = "Act as an expert financial analyst. Find the most recent 10-K and 10-Q SEC filings for the specified company. Summarize the key risks and opportunities from the 'Management's Discussion and Analysis' section of each filing into concise, detailed bullet points. Include at least two key points for both risks and opportunities from each filing type (10-K and 10-Q). Only return the summarized analysis text, followed by a list of citation URIs.";
            const userQuery = `Find the latest 10-K and 10-Q filings for the company with ticker ${ticker}.`;
            const apiKey = "";
            const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${FLASH_MODEL_NAME}:generateContent?key=${apiKey}`;

            const payload = {
                contents: [{ parts: [{ text: userQuery }] }],
                tools: [{ "google_search": {} }],
                systemInstruction: { parts: [{ text: systemPrompt }] },
            };

            try {
                const apiCall = async () => {
                    const response = await fetch(apiUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }

                    const result = await response.json();
                    const candidate = result.candidates?.[0];
                    const text = candidate?.content?.parts?.[0]?.text || "No detailed summary could be generated.";

                    let sources = [];
                    const groundingMetadata = candidate?.groundingMetadata;
                    if (groundingMetadata && groundingMetadata.groundingAttributions) {
                        sources = groundingMetadata.groundingAttributions
                            .map(attribution => ({
                                uri: attribution.web?.uri,
                                title: attribution.web?.title || 'External Source',
                            }))
                            .filter(source => source.uri);
                    }

                    renderFilingsDisplay(text, sources);
                    showMessage('success', `Filings summary generated for ${ticker}.`);
                };
                
                await withExponentialBackoff(apiCall);

            } catch (error) {
                console.error("SEC Filings API Error:", error);
                renderFilingsDisplay(`Failed to fetch or summarize filings for ${ticker}. Error: ${error.message}`, []);
                showMessage('error', `Failed to fetch SEC filings for ${ticker}.`);
            } finally {
                button.disabled = false;
                button.textContent = 'Analyze Filings';
            }
        }
        
        function renderFilingsDisplay(text, sources) {
            const resultsDiv = document.getElementById('filings-results');
            
            let sourceHtml = sources.length > 0 ? 
                `<h3 class="text-lg font-semibold mt-6 mb-2 text-gray-300">Cited Sources (${sources.length})</h3>
                <ul class="space-y-1 text-sm">
                    ${sources.map(source => 
                        `<li class="flex items-start">
                            <span class="text-yellow-400 mr-2 flex-shrink-0">&bull;</span>
                            <a href="${source.uri}" target="_blank" rel="noopener noreferrer" 
                               class="text-blue-400 hover:text-blue-300 truncate" title="${source.title}">
                               ${source.title}
                            </a> 
                        </li>`
                    ).join('')}
                </ul>` : 
                '<p class="text-sm text-gray-500 mt-6">No specific sources were cited by the model.</p>';

            resultsDiv.innerHTML = `
                <div class="mt-6 bg-gray-700 p-5 rounded-lg">
                    <h3 class="text-xl font-bold mb-3 text-yellow-200 border-b border-gray-600 pb-2">Analysis Summary</h3>
                    <div class="analysis-content text-gray-300 mb-6 space-y-4">
                        ${text}
                    </div>
                    ${sourceHtml}
                </div>
            `;
        }
        
        function renderSecFilingsTab() {
            const contentArea = document.getElementById('content-area');
            contentArea.innerHTML = `
                <div class="p-4 sm:p-6 md:p-8 bg-gray-900 min-h-full">
                    <h1 class="text-3xl font-extrabold mb-6 text-yellow-400 border-b border-gray-700 pb-3">
                        <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-3"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
                        SEC Filings Analyzer
                    </h1>
                    <div class="bg-gray-800 p-6 rounded-xl shadow-2xl">
                        <div class="flex flex-col sm:flex-row gap-3 mb-6">
                            <input
                                id="ticker-input"
                                type="text"
                                placeholder="Enter Ticker Symbol (e.g., AAPL)"
                                value="MSFT"
                                class="flex-1 p-3 rounded-lg bg-gray-700 border border-gray-600 text-white focus:ring-yellow-500 focus:border-yellow-500"
                                style="text-transform: uppercase;"
                            />
                            <button 
                                id="analyze-btn"
                                class="bg-yellow-600 hover:bg-yellow-700 text-white font-bold py-3 px-6 rounded-lg transition duration-150 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
                            >
                                Analyze Filings
                            </button>
                        </div>
                        
                        <div id="filings-results" class="text-center py-12 text-gray-400">
                            Enter a ticker and click "Analyze" to begin.
                        </div>
                    </div>
                </div>
            `;
            
            const tickerInput = document.getElementById('ticker-input');
            const analyzeBtn = document.getElementById('analyze-btn');

            const startAnalysis = () => {
                fetchSecFilings(tickerInput.value.toUpperCase());
            };
            
            analyzeBtn.addEventListener('click', startAnalysis);
            tickerInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') startAnalysis();
            });
            tickerInput.addEventListener('input', (e) => {
                tickerInput.value = e.target.value.toUpperCase();
            });

            // Auto-trigger MSFT analysis on load
            fetchSecFilings('MSFT');
        }


        // --- 2. WATCHLIST TAB LOGIC ---
        
        let currentWatchlist = [];
        let unsubscribeWatchlist = null;

        function getWatchlistCollectionRef() {
            if (!db || !userId) return null;
            return collection(db, 'artifacts', appId, 'public', 'data', 'stockWatchlists');
        }

        // Search API (Debounced)
        const performSearch = useDebounce(async (searchTerm) => {
            const searchResultsDiv = document.getElementById('search-results');
            if (!searchTerm) {
                searchResultsDiv.innerHTML = '';
                return;
            }

            searchResultsDiv.innerHTML = '<p class="text-center py-4 text-green-400">Searching...</p>';

            const systemPrompt = "You are a financial data provider. Respond to the user's search query for a stock ticker or company name with a JSON array of stock objects. Each object must contain 'ticker', 'companyName', 'currentPrice' (a mock USD value), and 'dailyChange' (a mock percentage string like '+1.50%'). Use real and popular stock data, but mock the price fields. Return ONLY the JSON array.";
            const userQuery = `Find stocks matching the ticker or name: "${searchTerm}".`;
            const apiKey = "";
            const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${FLASH_MODEL_NAME}:generateContent?key=${apiKey}`;

            const payload = {
                contents: [{ parts: [{ text: userQuery }] }],
                tools: [{ "google_search": {} }],
                systemInstruction: { parts: [{ text: systemPrompt }] },
                generationConfig: {
                    responseMimeType: "application/json",
                    responseSchema: {
                        type: "ARRAY",
                        items: {
                            type: "OBJECT",
                            properties: {
                                "ticker": { "type": "STRING" },
                                "companyName": { "type": "STRING" },
                                "currentPrice": { "type": "STRING" },
                                "dailyChange": { "type": "STRING" }
                            }
                        }
                    }
                }
            };

            try {
                const apiCall = async () => {
                    const response = await fetch(apiUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }

                    const result = await response.json();
                    const text = result.candidates?.[0]?.content?.parts?.[0]?.text;
                    const parsedJson = safeJsonParse(text);
                    
                    let stocks = Array.isArray(parsedJson) ? parsedJson : [];
                    
                    renderSearchResults(stocks);
                };
                
                await withExponentialBackoff(apiCall);

            } catch (error) {
                console.error("Stock search API error:", error);
                showMessage('error', `Search failed: ${error.message}`);
                searchResultsDiv.innerHTML = '<p class="text-center py-4 text-red-400">Error fetching stocks.</p>';
            }
        }, 500);

        // Firestore Watchlist Listener
        function startWatchlistListener() {
            if (unsubscribeWatchlist) unsubscribeWatchlist();
            
            if (!isAuthReady || !db || !userId) {
                renderWatchlist(currentWatchlist, false); // Render loading state
                return;
            }

            const watchlistRef = getWatchlistCollectionRef();
            if (!watchlistRef) return;

            const q = query(watchlistRef, where("userId", "==", userId));

            unsubscribeWatchlist = onSnapshot(q, (querySnapshot) => {
                const items = [];
                querySnapshot.forEach((doc) => {
                    items.push({ id: doc.id, ...doc.data() });
                });
                items.sort((a, b) => (a.timestamp?.seconds || 0) - (b.timestamp?.seconds || 0));
                currentWatchlist = items;
                renderWatchlist(items, true);
            }, (error) => {
                console.error("Firestore Watchlist Listener Error:", error);
                showMessage('error', 'Failed to load watchlist in real-time.');
            });
        }
        
        // Firestore Mutations
        async function addToWatchlist(stock) {
            if (!db || !userId) {
                showMessage('error', 'Database not connected.');
                return;
            }
            if (currentWatchlist.some(item => item.ticker === stock.ticker)) {
                showMessage('info', `${stock.ticker} is already on your watchlist.`);
                return;
            }
            const watchlistRef = getWatchlistCollectionRef();
            try {
                await addDoc(watchlistRef, {
                    userId: userId,
                    ticker: stock.ticker,
                    companyName: stock.companyName,
                    currentPrice: stock.currentPrice,
                    dailyChange: stock.dailyChange,
                    timestamp: serverTimestamp()
                });
                showMessage('success', `${stock.ticker} added to watchlist!`);
            } catch (error) {
                console.error("Error adding document: ", error);
                showMessage('error', `Failed to add ${stock.ticker}.`);
            }
        }

        async function removeFromWatchlist(itemId, ticker) {
            if (!db || !userId) {
                showMessage('error', 'Database not connected.');
                return;
            }
            const watchlistRef = getWatchlistCollectionRef();
            try {
                await deleteDoc(doc(watchlistRef, itemId));
                showMessage('success', `${ticker} removed from watchlist.`);
            } catch (error) {
                console.error("Error removing document: ", error);
                showMessage('error', 'Failed to remove item.');
            }
        }

        // --- WATCHLIST RENDERING ---

        function getChangeClass(change) {
            if (!change) return 'text-gray-400';
            return change.startsWith('+') ? 'text-green-400' : 'text-red-400';
        }

        function createStockCard(stock, isWatchlist) {
            const card = document.createElement('div');
            card.className = 'flex justify-between items-center p-3 bg-gray-700/50 rounded-lg shadow-md border border-gray-600/50 space-x-3 w-full';
            
            const isAdded = currentWatchlist.some(item => item.ticker === stock.ticker);

            card.innerHTML = `
                <div class='flex-1 min-w-0'>
                    <p class="text-xl font-extrabold text-white truncate">${stock.ticker}</p>
                    <p class="text-sm text-gray-400 truncate">${stock.companyName}</p>
                </div>
                <div class='flex flex-col items-end min-w-[120px]'>
                    <p class="font-bold text-lg text-indigo-300">${stock.currentPrice}</p>
                    <p class="text-sm font-medium ${getChangeClass(stock.dailyChange)}">
                        ${stock.dailyChange}
                    </p>
                </div>
            `;
            
            const actionContainer = document.createElement('div');
            
            if (isWatchlist) {
                actionContainer.innerHTML = `
                    <button class="remove-btn bg-red-600 hover:bg-red-700 text-white text-sm font-medium py-1 px-3 rounded-full transition duration-150 flex-shrink-0">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg> Remove
                    </button>
                `;
                const removeBtn = actionContainer.querySelector('.remove-btn');
                removeBtn.addEventListener('click', () => removeFromWatchlist(stock.id, stock.ticker));
            } else {
                const buttonText = isAdded ? 'Remove' : 'Add';
                const buttonClass = isAdded 
                    ? 'bg-red-600 hover:bg-red-700 text-white' 
                    : 'bg-green-600 hover:bg-green-700 text-white';

                actionContainer.innerHTML = `
                    <button class="action-btn text-sm font-medium py-1 px-3 rounded-full transition duration-150 flex-shrink-0 ${buttonClass}">
                        ${buttonText}
                    </button>
                `;
                const actionBtn = actionContainer.querySelector('.action-btn');
                actionBtn.addEventListener('click', () => {
                    if (isAdded) {
                        const existingItem = currentWatchlist.find(item => item.ticker === stock.ticker);
                        if (existingItem) removeFromWatchlist(existingItem.id, existingItem.ticker);
                    } else {
                        addToWatchlist(stock);
                    }
                });
            }
            card.appendChild(actionContainer);
            return card;
        }

        function renderSearchResults(stocks) {
            const searchResultsDiv = document.getElementById('search-results');
            searchResultsDiv.innerHTML = ''; 

            if (stocks.length === 0) {
                searchResultsDiv.innerHTML = '<p class="text-center py-4 text-gray-400">No results found. Try a different ticker or company.</p>';
                return;
            }

            stocks.forEach(stock => {
                searchResultsDiv.appendChild(createStockCard(stock, false));
            });
        }

        function renderWatchlist(items, isReady) {
            const watchlistDiv = document.getElementById('watchlist-items');
            watchlistDiv.innerHTML = '';
            
            if (!isReady) {
                watchlistDiv.innerHTML = '<p class="text-center py-4 text-yellow-400 font-medium">Establishing secure database connection...</p>';
                return;
            }

            if (items.length === 0) {
                watchlistDiv.innerHTML = '<p class="text-center py-4 text-gray-400">Your watchlist is empty! Add some stocks.</p>';
                return;
            }

            items.forEach(item => {
                watchlistDiv.appendChild(createStockCard(item, true));
            });
        }

        function renderStockWatchlistTab() {
            const contentArea = document.getElementById('content-area');
            contentArea.innerHTML = `
                <div class="p-4 sm:p-6 md:p-8 bg-gray-900 min-h-full">
                    <h1 class="text-3xl font-extrabold mb-6 text-green-400 border-b border-gray-700 pb-3">
                        <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-3"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><path d="M17 6h6v6"/></svg>
                        Stock Watchlist
                    </h1>

                    <div class="grid md:grid-cols-2 gap-8">
                        <!-- Search Panel -->
                        <div class="bg-gray-800 p-6 rounded-xl shadow-2xl">
                            <h2 class="text-xl font-bold mb-4 text-green-300 flex items-center">
                                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mr-2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg> Search Stocks
                            </h2>
                            <input
                                id="search-input"
                                type="text"
                                placeholder="e.g., AAPL, GOOG, Tesla"
                                class="w-full p-3 mb-4 rounded-lg bg-gray-700 border border-gray-600 text-white focus:ring-green-500 focus:border-green-500"
                            />
                            <div id="search-results" class="space-y-3 h-[300px] overflow-y-auto custom-scrollbar pr-2">
                                <!-- Search results will be rendered here -->
                            </div>
                        </div>

                        <!-- Watchlist Panel -->
                        <div class="bg-gray-800 p-6 rounded-xl shadow-2xl">
                            <h2 class="text-xl font-bold mb-4 text-indigo-300">My Watchlist (<span id="watchlist-count">0</span>)</h2>
                            <div id="watchlist-items" class="space-y-3 h-[300px] overflow-y-auto custom-scrollbar pr-2">
                                <!-- Watchlist items will be rendered here -->
                            </div>
                        </div>
                    </div>
                </div>
            `;
            
            const searchInput = document.getElementById('search-input');
            searchInput.addEventListener('input', (e) => performSearch(e.target.value));

            // Initial rendering of watchlist (starts the listener)
            startWatchlistListener();
            document.getElementById('watchlist-items').addEventListener('DOMNodeInserted', () => {
                document.getElementById('watchlist-count').textContent = currentWatchlist.length;
            });
            document.getElementById('watchlist-items').addEventListener('DOMNodeRemoved', () => {
                document.getElementById('watchlist-count').textContent = currentWatchlist.length;
            });
            
            // Re-render the initial empty search state
            renderSearchResults([]);
        }

        // --- GLOBAL NAVIGATION AND ROUTING ---

        const tabConfig = {
            'main': { title: 'Dashboard', icon: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18.7 8l-5.1 5.2-2.8-2.7-4.4 4.8"/></svg>', render: renderDashboard, color: 'bg-indigo-600', text: 'text-indigo-400' },
            'watchlist': { title: 'Stock Watchlist', icon: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><path d="M17 6h6v6"/></svg>', render: renderStockWatchlistTab, color: 'bg-green-600', text: 'text-green-400' },
            'filings': { title: 'SEC Filings', icon: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>', render: renderSecFilingsTab, color: 'bg-yellow-600', text: 'text-yellow-400' },
        };
        
        function renderDashboard() {
            const contentArea = document.getElementById('content-area');
            contentArea.innerHTML = `
                <div class="p-8 bg-gray-900 min-h-full flex flex-col items-center justify-center">
                    <h1 class="text-4xl font-bold text-indigo-400 mb-4">Financial Dashboard</h1>
                    <p class="text-gray-400 text-lg text-center max-w-md">
                        This integrated tool provides real-time stock tracking and SEC filing analysis.
                    </p>
                    <p class="text-gray-400 text-lg text-center max-w-md mt-2">
                        Use the sidebar to navigate between features.
                    </p>
                </div>
            `;
        }
        
        function renderTab(tabName) {
            activeTab = tabName;
            
            const config = tabConfig[tabName];
            if (!config) return;

            // Update mobile header
            document.getElementById('current-tab-title').textContent = config.title;
            
            // Update sidebar buttons
            const navItems = document.getElementById('nav-items');
            navItems.innerHTML = Object.entries(tabConfig).map(([key, item]) => `
                <button
                    data-tab="${key}"
                    class="tab-btn flex items-center w-full px-4 py-2 rounded-lg transition duration-150 ${
                        key === tabName 
                            ? `${item.color} text-white shadow-lg` 
                            : 'text-gray-300 hover:bg-gray-700'
                    }"
                >
                    ${item.icon}
                    <span class="ml-3">${item.title}</span>
                </button>
            `).join('');

            document.querySelectorAll('.tab-btn').forEach(button => {
                button.addEventListener('click', () => {
                    renderTab(button.getAttribute('data-tab'));
                    closeSidebar();
                });
            });

            // Render content
            config.render();
        }
        
        // --- SIDEBAR CONTROL ---
        
        function openSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.remove('-translate-x-full', 'w-0');
            sidebar.classList.add('translate-x-0', 'w-64');
        }

        function closeSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.remove('translate-x-0', 'w-64');
            // Use setTimeout to ensure the transition completes before resetting width on desktop
            if (window.innerWidth < 768) {
                sidebar.classList.add('-translate-x-full', 'w-0');
            }
        }
        
        // --- ENTRY POINT ---
        window.onload = function() {
            // Setup mobile sidebar listeners
            document.getElementById('sidebar-open-btn').addEventListener('click', openSidebar);
            document.getElementById('sidebar-close-btn').addEventListener('click', closeSidebar);
            
            // Initialize Firebase and start the application
            initFirebase();
            renderTab(activeTab); // Initial render (will refresh after auth is ready)
        };
    </script>
</body>
</html>
