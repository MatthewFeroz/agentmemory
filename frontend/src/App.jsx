// =============================================================================
// App.jsx — Main chat interface for the Redis DevRel Content Strategist
// =============================================================================
// Single-component React app with three memory modes, each with its own
// color theme based on the official Redis brand palette.
//
// Mode 1: No Memory      — classic Redis red (#FF4438) on dark (#091A23)
// Mode 2: Short-Term     — inverted: light background, red accents
// Mode 3: Long-Term      — modern Redis: chartreuse yellow (#DCFF1E) + navy
//
// Talks to our FastAPI backend at /api/chat via the Vite dev proxy.
// =============================================================================

import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// --- Configuration -----------------------------------------------------------
const API_URL = "/api";

// --- Mode definitions --------------------------------------------------------
// Each mode has an id, a display label, a CSS class for theming, and
// descriptive text shown in the empty state. The session_id changes per mode
// so conversations don't bleed across modes during the demo.
const MODES = [
  {
    id: "none",
    label: "No Memory",
    themeClass: "",                          // default theme (no extra class)
    heading: "No Memory Mode",
    description:
      "Each message is independent. The assistant has no context of previous messages — every request starts from zero.",
    session: "demo-no-memory",
  },
  {
    id: "short-term",
    label: "Short-Term Memory",
    themeClass: "theme-short-term",
    heading: "Short-Term Memory",
    description:
      "The assistant remembers everything from this session. Conversation history is loaded from Redis on every request.",
    session: "demo-short-term",
  },
  {
    id: "long-term",
    label: "Long-Term Memory",
    themeClass: "theme-long-term",
    heading: "Long-Term Memory",
    description:
      "The assistant remembers you across sessions. Facts and preferences persist in Redis even after you start a new conversation.",
    session: "demo-long-term",
  },
];

// --- Suggestion chips — placeholder prompts for the demo --------------------
// These appear in the empty state. Clicking one sends that message directly.
// Kept as placeholders so you can craft the narrative story for the demo later.
const SUGGESTIONS = [
  "Template prompt one",
  "Template prompt two",
  "Template prompt three",
];

// --- Utility: format a timestamp for display ---------------------------------
function formatMessageTime(timestamp) {
  if (!timestamp) return null;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function App() {
  // --- State -----------------------------------------------------------------
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  // activeMode is the index into MODES (0 = no memory, 1 = short-term, 2 = long-term)
  const [activeMode, setActiveMode] = useState(0);

  // Derived: the current mode object
  const mode = MODES[activeMode];

  // --- Refs ------------------------------------------------------------------
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // --- Auto-scroll to bottom when messages change ----------------------------
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // --- Apply the theme class to the document body ----------------------------
  // We put the theme class on <body> so that CSS variables cascade to everything,
  // including elements outside the React tree (scrollbars, browser chrome hints).
  useEffect(() => {
    // Remove all theme classes first, then add the active one
    document.body.classList.remove("theme-short-term", "theme-long-term");
    if (mode.themeClass) {
      document.body.classList.add(mode.themeClass);
    }
  }, [mode.themeClass]);

  // --- Switch mode -----------------------------------------------------------
  // Clears the conversation when switching modes so each demo starts fresh.
  const switchMode = (index) => {
    if (index === activeMode) return; // already active
    setActiveMode(index);
    setMessages([]);
    setInput("");
  };

  // --- Send a message to the backend -----------------------------------------
  const sendMessage = async (overrideText) => {
    const trimmed = (overrideText || input).trim();
    if (!trimmed || loading) return;

    // Optimistic UI: show the user message immediately
    const userMessage = {
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: mode.session,
        }),
      });

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }
      const data = await response.json();

      const assistantMessage = {
        role: "assistant",
        content: data.response,
        timestamp: new Date().toISOString(),
        usage: data.usage,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      const errorMessage = {
        role: "assistant",
        content: `Error: ${error.message}. Is the backend running on port 8000?`,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  // --- Handle Enter key to send ----------------------------------------------
  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // --- Render ----------------------------------------------------------------
  return (
    <div className="app">
      {/* --- Header --------------------------------------------------------- */}
      <header className="header">
        <div className="header-dot" />
        <h1>
          <span className="header-redis">Redis</span> DevRel
        </h1>
        <span className="header-subtitle">AI Content Strategist</span>
      </header>

      {/* --- Mode switcher -------------------------------------------------- */}
      <div className="mode-bar">
        <div className="mode-switcher">
          {MODES.map((m, i) => (
            <button
              key={m.id}
              className={`mode-btn${i === activeMode ? " active" : ""}`}
              onClick={() => switchMode(i)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* --- Messages area -------------------------------------------------- */}
      <div className="messages">
        {/* Empty state: mode description + suggestion chips */}
        {messages.length === 0 && !loading && (
          <div className="empty-state">
            <h2>{mode.heading}</h2>
            <p>{mode.description}</p>
            <div className="suggestion-chips">
              {SUGGESTIONS.map((text) => (
                <button
                  key={text}
                  className="chip"
                  onClick={() => sendMessage(text)}
                >
                  {text}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Render each message as a bubble */}
        {messages.map((msg, i) => {
          const timeLabel = formatMessageTime(msg.timestamp);
          return (
            <div key={i} className={`message-row ${msg.role}`}>
              <div className={`message-stack ${msg.role}`}>
                <div className={`message ${msg.role}`}>
                  {msg.role === "assistant" ? (
                    <div className="message-md">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ node, ...props }) => {
                            void node;
                            return (
                              <a
                                {...props}
                                target="_blank"
                                rel="noopener noreferrer"
                              />
                            );
                          },
                        }}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>

                <div className={`message-meta ${msg.role}`}>
                  {timeLabel && <span>{timeLabel}</span>}
                  {msg.role === "assistant" && msg.usage && (
                    <span>
                      {msg.usage.input_tokens} input / {msg.usage.output_tokens}{" "}
                      output tokens
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}

        {/* Loading indicator */}
        {loading && (
          <div className="loading">
            <div className="loading-dot" />
            <div className="loading-dot" />
            <div className="loading-dot" />
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* --- Input area ----------------------------------------------------- */}
      <div className="input-area">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about content ideas, editorial planning, audience strategy..."
          disabled={loading}
          autoFocus
        />
        <button onClick={() => sendMessage()} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

export default App;
