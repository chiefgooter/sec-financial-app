import React, { useState, useEffect, useCallback } from 'react';
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously, signInWithCustomToken, onAuthStateChanged } from 'firebase/auth';
import { getFirestore, collection, query, where, onSnapshot, addDoc, deleteDoc, doc, serverTimestamp } from 'firebase/firestore';
import { Menu, TrendingUp, Search, X, FileText, LayoutDashboard } from 'lucide-react';

// Define the model name safely
const FLASH_MODEL_NAME = "gemini-2.5-flash-preview-0" + "9" + "-2025"; 

// Global variables provided by the Canvas environment
const appId = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id';
const firebaseConfig = typeof __firebase_config !== 'undefined' ? JSON.parse(__firebase_config) : null;
const initialAuthToken = typeof __initial_auth_token !== 'undefined' ? __initial_auth_token : null;

// --- UTILITY FUNCTIONS ---

const useDebounce = (func, delay) => {
    const handler = React.useRef(null);
    const callback = React.useRef(func);

    useEffect(() => {
        callback.current = func;
    }, [func]);

    return useCallback((...args) => {
        if (handler.current) {
            clearTimeout(handler.current);
        }
        handler.current = setTimeout(() => {
            callback.current(...args);
        }, delay);
    }, [delay]);
};

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

/**
 * Safely parses a string that might contain JSON, stripping markdown fences (```json) first.
 * @param {string} text 
 * @returns {object | null}
 */
const safeJsonParse = (text) => {
    if (!text) return null;
    
    // 1. Remove markdown code fences if they exist (e.g., ```json ... ```)
    let cleanedText = text.trim();
    if (cleanedText.startsWith('```')) {
        const lines = cleanedText.split('\n');
        // Check for common markers like '```json' or '```'
        if (lines.length > 1 && (lines[0].startsWith('```json') || lines[0] === '```')) {
            lines.shift(); // Remove starting fence
            if (lines[lines.length - 1] === '```') {
                lines.pop(); // Remove ending fence
            }
        }
        cleanedText = lines.join('\n').trim();
    }
    
    // 2. Attempt standard JSON parsing
    try {
        return JSON.parse(cleanedText);
    } catch (e) {
        console.error("Failed to parse JSON after cleanup:", e, "Original text:", text);
        return null; 
    }
};


// --- 1. SEC FILINGS Tab Component ---

const SecFilingsTab = ({ setMessage }) => {
    const [ticker, setTicker] = useState('MSFT');
    const [filingData, setFilingData] = useState(null); // Stores {text, sources}
    const [isLoading, setIsLoading] = useState(false);
    const [isInitialLoad, setIsInitialLoad] = useState(true);

    const fetchSecFilings = useCallback(async () => {
        if (!ticker) {
            setMessage({ type: 'error', text: 'Please enter a ticker symbol to search.' });
            return;
        }

        setIsLoading(true);
        setFilingData(null);

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

                setFilingData({ text, sources });
                setMessage({ type: 'success', text: `Filings summary generated for ${ticker}.` });
            };
            
            await withExponentialBackoff(apiCall);

        } catch (error) {
            console.error("SEC Filings API Error:", error);
            setFilingData({ text: `Failed to fetch or summarize filings for ${ticker}. Error: ${error.message}`, sources: [] });
            setMessage({ type: 'error', text: `Failed to fetch SEC filings for ${ticker}.` });
        } finally {
            setIsLoading(false);
            setIsInitialLoad(false);
        }
    }, [ticker, setMessage]);
    
    useEffect(() => {
        if (ticker && isInitialLoad) {
            fetchSecFilings();
        }
    }, [ticker, isInitialLoad, fetchSecFilings]);

    const FilingsDisplay = () => {
        if (isLoading) {
            return <div className="text-center py-12 text-yellow-300 font-medium text-lg">Analyzing filings... please wait.</div>;
        }

        if (isInitialLoad) {
            return <div className="text-center py-12 text-gray-400">Enter a ticker and click "Analyze" to begin.</div>;
        }

        if (filingData) {
            return (
                <div className="mt-6 bg-gray-700 p-5 rounded-lg">
                    <h3 className="text-xl font-bold mb-3 text-yellow-200 border-b border-gray-600 pb-2">Analysis Summary</h3>
                    
                    {/* Inject Analysis Content from LLM. 
                        Note: Due to parser issues, all custom CSS for p, ul, h3 tags in this output 
                        has been removed. It will use browser defaults for structure.
                    */}
                    <div className="analysis-content text-gray-300 mb-6 space-y-4" dangerouslySetInnerHTML={{ __html: filingData.text }} />
                    
                    <h3 className="text-lg font-semibold mt-4 mb-2 text-gray-300">Cited Sources ({filingData.sources.length})</h3>
                    <ul className="space-y-1 text-sm">
                        {filingData.sources.map((source, index) => (
                            <li key={index} className="flex items-start">
                                {/* Using &bull; entity for maximum parser compatibility */}
                                <span className="text-yellow-400 mr-2 flex-shrink-0" dangerouslySetInnerHTML={{ __html: '&bull;' }} />
                                <a 
                                    href={source.uri} 
                                    target="_blank" 
                                    rel="noopener noreferrer" 
                                    className="text-blue-400 hover:text-blue-300 truncate"
                                    title={source.title}
                                >
                                    {source.title}
                                </a> 
                            </li>
                        ))}
                    </ul>
                </div>
            );
        }

        return null;
    };


    return (
        <div className="p-4 sm:p-6 md:p-8 bg-gray-900 min-h-full">
            <h1 className="text-3xl font-extrabold mb-6 text-yellow-400 border-b border-gray-700 pb-3">
                <FileText className="inline-block mr-3" size={28} />
                SEC Filings Analyzer
            </h1>
            <div className="bg-gray-800 p-6 rounded-xl shadow-2xl">
                <div className="flex flex-col sm:flex-row gap-3 mb-6">
                    <input
                        type="text"
                        placeholder="Enter Ticker Symbol (e.g., AAPL)"
                        value={ticker}
                        onChange={(e) => setTicker(e.target.toUpperCase())}
                        className="flex-1 p-3 rounded-lg bg-gray-700 border border-gray-600 text-white focus:ring-yellow-500 focus:border-yellow-500"
                        onKeyPress={(e) => { if (e.key === 'Enter') fetchSecFilings(); }}
                    />
                    <button 
                        onClick={fetchSecFilings}
                        disabled={isLoading}
                        className="bg-yellow-600 hover:bg-yellow-700 text-white font-bold py-3 px-6 rounded-lg transition duration-150 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
                    >
                        {isLoading ? 'Analyzing...' : 'Analyze Filings'}
                    </button>
                </div>
                
                <FilingsDisplay />
            </div>
        </div>
    );
};


// --- 2. Watchlist Component ---

const StockWatchlistTab = ({ db, userId, isAuthReady, setMessage }) => {
    
    // UI & Data State
    const [searchTerm, setSearchTerm] = useState('');
    const [results, setResults] = useState([]);
    const [watchlist, setWatchlist] = useState([]);
    const [isLoading, setIsLoading] = useState(false);

    const debouncedSearchTerm = useDebounce(searchTerm, 500);

    // Helper to construct the Watchlist Collection Path
    const getWatchlistCollectionRef = useCallback((dbInstance, uid) => {
        if (!dbInstance || !uid) return null;
        // Public path: /artifacts/{appId}/public/data/stockWatchlists
        return collection(dbInstance, 'artifacts', appId, 'public', 'data', 'stockWatchlists');
    }, []);

    // --- STOCK SEARCH API CALL ---
    const searchStocks = useCallback(async () => {
        if (!debouncedSearchTerm) {
            setResults([]);
            return;
        }

        const systemPrompt = "You are a financial data provider. Respond to the user's search query for a stock ticker or company name with a JSON array of stock objects. Each object must contain 'ticker', 'companyName', 'currentPrice' (a mock USD value), and 'dailyChange' (a mock percentage string like '+1.50%'). Use real and popular stock data, but mock the price fields.";
        const userQuery = `Find stocks matching the ticker or name: "${debouncedSearchTerm}". Provide only the JSON array.`;
        const apiKey = "";
        
        const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${FLASH_MODEL_NAME}:generateContent?key=${apiKey}`;

        setIsLoading(true);
        setResults([]);

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
                            "ticker": { "type": "STRING", "description": "The stock market ticker symbol (e.g., AAPL)." },
                            "companyName": { "type": "STRING", "description": "The full name of the company." },
                            "currentPrice": { "type": "STRING", "description": "A mock current price in USD (e.g., $185.20)." },
                            "dailyChange": { "type": "STRING", "description": "A mock daily percentage change (e.g., +1.50% or -0.75%)." }
                        },
                        required: ["ticker", "companyName", "currentPrice", "dailyChange"]
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

                if (text) {
                    const parsedJson = safeJsonParse(text);

                    if (Array.isArray(parsedJson)) {
                        setResults(parsedJson.map(stock => ({
                            ...stock,
                            id: stock.ticker, 
                        })));
                    } else if (parsedJson) {
                        // Handle case where LLM returns a single object instead of an array
                        setResults([{...parsedJson, id: parsedJson.ticker}]);
                    }
                    
                } else {
                    throw new Error("Model response was empty or malformed.");
                }
            };
            
            await withExponentialBackoff(apiCall);

        } catch (error) {
            console.error("Stock search API error:", error);
            setMessage({ 
                type: 'error', 
                text: error.message.includes('Unexpected end of input') 
                    ? 'Search failed due to incomplete data (network issue).' 
                    : `Search failed: ${error.message}` 
            });
        } finally {
            setIsLoading(false);
        }
    }, [debouncedSearchTerm, setMessage]);

    // Trigger search when debounced term changes
    useEffect(() => {
        searchStocks();
    }, [debouncedSearchTerm, searchStocks]);

    // --- FIREBASE WATCHLIST LISTENER (onSnapshot) ---
    useEffect(() => {
        if (!isAuthReady || !db || !userId) {
            setWatchlist([]);
            return;
        }

        const watchlistRef = getWatchlistCollectionRef(db, userId);
        if (!watchlistRef) return;

        // Query documents owned by the current user
        const q = query(watchlistRef, where("userId", "==", userId));

        const unsubscribe = onSnapshot(q, (querySnapshot) => {
            const items = [];
            querySnapshot.forEach((doc) => {
                items.push({ id: doc.id, ...doc.data() });
            });
            // Sort by timestamp for consistent order
            items.sort((a, b) => (a.timestamp?.seconds || 0) - (b.timestamp?.seconds || 0));
            setWatchlist(items);
        }, (error) => {
            console.error("Firestore Watchlist Listener Error:", error);
            setMessage({ type: 'error', text: 'Failed to load watchlist in real-time.' });
        });

        return () => unsubscribe();
    }, [db, userId, isAuthReady, getWatchlistCollectionRef, setMessage]);

    // --- FIREBASE MUTATIONS ---

    const addToWatchlist = useCallback(async (stock) => {
        if (!db || !userId) {
            setMessage({ type: 'error', text: 'Database not connected. Please wait for authentication.' });
            return;
        }

        if (watchlist.some(item => item.ticker === stock.ticker)) {
            setMessage({ type: 'info', text: `${stock.ticker} is already on your watchlist.` });
            return;
        }

        const watchlistRef = getWatchlistCollectionRef(db, userId);

        try {
            await addDoc(watchlistRef, {
                userId: userId,
                ticker: stock.ticker,
                companyName: stock.companyName,
                currentPrice: stock.currentPrice,
                dailyChange: stock.dailyChange,
                timestamp: serverTimestamp()
            });
            setMessage({ type: 'success', text: `${stock.ticker} added to watchlist!` });
        } catch (error) {
            console.error("Error adding document: ", error);
            setMessage({ type: 'error', text: `Failed to add ${stock.ticker}.` });
        }
    }, [db, userId, watchlist, getWatchlistCollectionRef, setMessage]);

    const removeFromWatchlist = useCallback(async (itemId, ticker) => {
        if (!db || !userId) {
            setMessage({ type: 'error', text: 'Database not connected. Please wait for authentication.' });
            return;
        }

        const watchlistRef = getWatchlistCollectionRef(db, userId);

        try {
            await deleteDoc(doc(watchlistRef, itemId));
            setMessage({ type: 'success', text: `${ticker} removed from watchlist.` });
        } catch (error) {
            console.error("Error removing document: ", error);
            setMessage({ type: 'error', text: 'Failed to remove item.' });
        }
    }, [db, userId, getWatchlistCollectionRef, setMessage]);


    // UI Rendering helpers
    const getButtonStatus = (ticker) => {
        return watchlist.some(item => item.ticker === ticker) ? 'Remove' : 'Add';
    };

    const handleAction = (stock) => {
        const existingItem = watchlist.find(item => item.ticker === stock.ticker);
        if (existingItem) {
            removeFromWatchlist(existingItem.id, existingItem.ticker); 
        } else {
            addToWatchlist(stock);
        }
    };

    const getChangeClass = (change) => {
        if (!change) return 'text-gray-400';
        return change.startsWith('+') ? 'text-green-400' : 'text-red-400';
    };

    const StockCard = ({ stock, isWatchlist }) => {
        return (
            <div className="flex justify-between items-center p-3 bg-gray-700/50 rounded-lg shadow-md border border-gray-600/50 space-x-3 w-full">
                <div className='flex-1 min-w-0'>
                    <p className="text-xl font-extrabold text-white truncate">{stock.ticker}</p>
                    <p className="text-sm text-gray-400 truncate">{stock.companyName}</p>
                </div>

                <div className='flex flex-col items-end min-w-[120px]'>
                    <p className="font-bold text-lg text-indigo-300">{stock.currentPrice}</p>
                    <p className={`text-sm font-medium ${getChangeClass(stock.dailyChange)}`}>
                        {stock.dailyChange}
                    </p>
                </div>

                {isWatchlist ? (
                    <button
                        onClick={() => removeFromWatchlist(stock.id, stock.ticker)}
                        className="bg-red-600 hover:bg-red-700 text-white text-sm font-medium py-1 px-3 rounded-full transition duration-150 flex-shrink-0"
                    >
                        <X size={16} className='inline-block mr-1'/> Remove
                    </button>
                ) : (
                    <button
                        onClick={() => handleAction(stock)}
                        className={`text-sm font-medium py-1 px-3 rounded-full transition duration-150 flex-shrink-0 ${
                            getButtonStatus(stock.ticker) === 'Remove'
                            ? 'bg-red-600 hover:bg-red-700 text-white'
                            : 'bg-green-600 hover:bg-green-700 text-white'
                        }`}
                    >
                        {getButtonStatus(stock.ticker) === 'Remove' ? 'Remove' : 'Add'}
                    </button>
                )}
            </div>
        );
    };

    return (
        <div className="p-4 sm:p-6 md:p-8 bg-gray-900 min-h-full">
            <h1 className="text-3xl font-extrabold mb-6 text-green-400 border-b border-gray-700 pb-3">
                <TrendingUp className="inline-block mr-3" size={28} />
                Stock Watchlist
            </h1>

            <div className="grid md:grid-cols-2 gap-8">
                {/* Search Panel */}
                <div className="bg-gray-800 p-6 rounded-xl shadow-2xl">
                    <h2 className="text-xl font-bold mb-4 text-green-300 flex items-center"><Search size={20} className='mr-2'/> Search Stocks</h2>
                    <input
                        type="text"
                        placeholder="e.g., AAPL, GOOG, Tesla"
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="w-full p-3 mb-4 rounded-lg bg-gray-700 border border-gray-600 text-white focus:ring-green-500 focus:border-green-500"
                    />
                    
                    {/* Note: custom-scrollbar class relies on global styles in App component */}
                    <div className="space-y-3 h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                        {isLoading && <p className="text-center py-4 text-green-400">Searching...</p>}
                        {!isLoading && results.length === 0 && searchTerm.length > 0 && (
                            <p className="text-center py-4 text-gray-400">No results found. Try a different ticker or company.</p>
                        )}
                        {results.map((stock) => (
                            <StockCard key={stock.id} stock={stock} isWatchlist={false} />
                        ))}
                    </div>
                </div>

                {/* Watchlist Panel */}
                <div className="bg-gray-800 p-6 rounded-xl shadow-2xl">
                    <h2 className="text-xl font-bold mb-4 text-indigo-300">My Watchlist ({watchlist.length})</h2>
                    
                    {/* Note: custom-scrollbar class relies on global styles in App component */}
                    <div className="space-y-3 h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                        {!isAuthReady && (
                            <p className="text-center py-4 text-yellow-400 font-medium">
                                Establishing secure database connection...
                            </p>
                        )}
                        {isAuthReady && watchlist.length === 0 && (
                            <p className="text-center py-4 text-gray-400">Your watchlist is empty! Add some stocks.</p>
                        )}
                        {isAuthReady && watchlist.map((item) => (
                            <StockCard 
                                key={item.id} 
                                stock={item}
                                isWatchlist={true} 
                            />
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
};


// --- 3. Main Application Wrapper (Handles Tabs/Routing) ---

const App = () => {
    // Start on the SEC Filings tab since that was the previous focus
    const [activeTab, setActiveTab] = useState('filings'); 
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);
    
    // Global State for Firebase and Messaging
    const [db, setDb] = useState(null);
    const [userId, setUserId] = useState(null);
    const [isAuthReady, setIsAuthReady] = useState(false);
    const [message, setMessage] = useState(null);

    // --- FIREBASE INITIALIZATION AND AUTHENTICATION ---
    useEffect(() => {
        if (!firebaseConfig) {
            setMessage({ type: 'error', text: 'Firebase configuration is missing.' });
            return;
        }

        try {
            const app = initializeApp(firebaseConfig);
            const firestore = getFirestore(app);
            const firebaseAuth = getAuth(app);

            setDb(firestore);
            
            const signInUser = async () => {
                try {
                    if (initialAuthToken) {
                        await signInWithCustomToken(firebaseAuth, initialAuthToken);
                    } else {
                        await signInAnonymously(firebaseAuth);
                    }
                } catch (error) {
                    console.error("Firebase Auth Error:", error);
                    setMessage({ type: 'error', text: `Authentication failed: ${error.message}` });
                }
            };

            const unsubscribeAuth = onAuthStateChanged(firebaseAuth, (user) => {
                if (user) {
                    setUserId(user.uid);
                } else {
                    signInUser();
                }
                setIsAuthReady(true);
            });

            return () => unsubscribeAuth();
        } catch (error) {
            console.error("Firebase Initialization Error:", error);
            setMessage({ type: 'error', text: `Firebase init failed: ${error.message}` });
        }
    }, []);


    const renderContent = () => {
        // Pass necessary props down to the child components
        const componentProps = { db, userId, isAuthReady, setMessage };

        switch (activeTab) {
            case 'watchlist':
                return <StockWatchlistTab {...componentProps} />;
            case 'filings':
                return <SecFilingsTab {...componentProps} />;
            case 'main':
            default:
                return (
                    <div className="p-8 bg-gray-900 min-h-full flex flex-col items-center justify-center">
                        <h1 className="text-4xl font-bold text-indigo-400 mb-4">Financial Dashboard</h1>
                        <p className="text-gray-400 text-lg text-center max-w-md">
                            Use the sidebar to navigate between the Stock Watchlist and the SEC Filings Analyzer.
                        </p>
                        <p className="mt-6 text-sm text-gray-500 break-all">
                            <span className="font-bold text-gray-400 mr-1">User ID:</span> {userId || 'N/A'}
                        </p>
                    </div>
                );
        }
    };
    
    // Style for responsive sidebar toggle
    const sidebarClasses = isSidebarOpen 
        ? "translate-x-0 w-64" 
        : "-translate-x-full w-0 md:translate-x-0 md:w-64";

    return (
        <div className="flex h-screen bg-gray-900 text-white">
            
            {/* Sidebar (Navigation) */}
            <div className={`fixed inset-y-0 left-0 transform transition-all duration-300 ease-in-out z-30 
                             ${sidebarClasses} bg-gray-800 border-r border-gray-700 shadow-xl md:relative md:flex-shrink-0`}>
                
                <div className="p-4 flex flex-col h-full">
                    <div className="flex justify-between items-center mb-6">
                        <h2 className="text-xl font-bold text-indigo-400">Financial Tools</h2>
                        <button 
                            className="text-gray-400 md:hidden p-1 rounded-full hover:bg-gray-700"
                            onClick={() => setIsSidebarOpen(false)}
                        >
                            <X size={24} />
                        </button>
                    </div>

                    {/* Nav Items */}
                    <nav className="space-y-2 flex-grow">
                        {/* Dashboard Tab */}
                        <button
                            onClick={() => { setActiveTab('main'); setIsSidebarOpen(false); }}
                            className={`flex items-center w-full px-4 py-2 rounded-lg transition duration-150 ${
                                activeTab === 'main' 
                                    ? 'bg-indigo-600 text-white shadow-lg' 
                                    : 'text-gray-300 hover:bg-gray-700'
                            }`}
                        >
                            <LayoutDashboard size={20} className="mr-3" />
                            Dashboard
                        </button>

                        {/* Watchlist Tab */}
                        <button
                            onClick={() => { setActiveTab('watchlist'); setIsSidebarOpen(false); }}
                            className={`flex items-center w-full px-4 py-2 rounded-lg transition duration-150 ${
                                activeTab === 'watchlist' 
                                    ? 'bg-green-600 text-white shadow-lg' 
                                    : 'text-gray-300 hover:bg-gray-700'
                            }`}
                        >
                            <TrendingUp size={20} className="mr-3" />
                            Stock Watchlist
                        </button>

                         {/* SEC Filings Tab */}
                         <button
                            onClick={() => { setActiveTab('filings'); setIsSidebarOpen(false); }}
                            className={`flex items-center w-full px-4 py-2 rounded-lg transition duration-150 ${
                                activeTab === 'filings' 
                                    ? 'bg-yellow-600 text-white shadow-lg' 
                                    : 'text-gray-300 hover:bg-gray-700'
                            }`}
                        >
                            <FileText size={20} className="mr-3" />
                            SEC Filings
                        </button>

                    </nav>

                    {/* Footer/Status */}
                    <div className="mt-4 pt-4 border-t border-gray-700">
                         <p className="text-sm font-mono break-all text-gray-500">
                             <span className="font-bold text-gray-400 mr-1">User ID:</span> {userId ? `${userId.substring(0, 8)}...` : 'N/A'}
                         </p>
                    </div>
                </div>
            </div>

            {/* Main Content Area */}
            <div className="flex-1 flex flex-col overflow-y-auto">
                {/* Mobile Header for Toggle */}
                <header className="bg-gray-800 p-4 md:hidden flex justify-between items-center border-b border-gray-700 z-20">
                    <button 
                        className="p-1 rounded-full hover:bg-gray-700"
                        onClick={() => setIsSidebarOpen(true)}
                    >
                        <Menu size={24} className="text-white" />
                    </button>
                    <h1 className="text-xl font-bold text-indigo-400">
                        {activeTab === 'main' ? 'Dashboard' : activeTab === 'watchlist' ? 'Watchlist' : 'Filings'}
                    </h1>
                </header>

                {/* Content */}
                <main className="flex-1 overflow-y-auto">
                    {/* Message Box (Global) */}
                    {message && (
                        // Used string concatenation to bypass previous parser error
                        <div 
                            className={"p-3 mx-auto mt-4 max-w-4xl rounded-lg shadow-md border z-10 " + (
                                message.type === 'error' ? 'bg-red-800 border-red-600' :
                                message.type === 'success' ? 'bg-green-800 border-green-600' :
                                'bg-blue-800 border-blue-600'
                            )} 
                            onClick={() => setMessage(null)}
                        >
                            <p className="font-medium text-center">{message.text}</p>
                        </div>
                    )}
                    {renderContent()}
                </main>
            </div>

            {/* Global Style Block (Custom CSS - only scrollbar remaining, minimal syntax) */}
            <style>{`
                /* Custom Scrollbar Styles for the Lists */
                .custom-scrollbar::-webkit-scrollbar {
                    width: 8px;
                }
                .custom-scrollbar::-webkit-scrollbar-track {
                    background: #374151;
                    border-radius: 10px;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb {
                    background: #34d399;
                    border-radius: 10px;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb:hover {
                    background: #10b981; 
                }
            `}</style>
        </div>
    );
};

export default App;
