// =============================================================================
// App.jsx — Main chat interface for the Redis DevRel Content Strategist
// =============================================================================
// This is the entire frontend in a single component. It's intentionally simple:
//   - A header with the app title
//   - A scrollable message area showing the conversation
//   - A text input + send button at the bottom
//
// It talks to our FastAPI backend at http://localhost:8000/chat via fetch().
// =============================================================================

import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// --- Configuration -----------------------------------------------------------
// The URL of our FastAPI backend. In development, Vite runs on port 5173 and
// the backend runs on port 8000. We've set up a Vite proxy (in vite.config.js)
// so we can just use "/api/chat" instead of "http://localhost:8000/chat".
// This avoids CORS issues during development.
const API_URL = "/api";

function formatMessageTime(timestamp) {
  if (!timestamp) return null;

  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;

  return date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function App() {
  // --- State -----------------------------------------------------------------
  // messages: array of { role: "user"|"assistant", content: "...", usage?: {} }
  // Each message matches the shape we get from the Anthropic API, which makes
  // it easy to display and (later) to send back as conversation history.
  const [messages, setMessages] = useState([]);

  // input: the current text in the input field (controlled component)
  const [input, setInput] = useState("");

  // loading: true while we're waiting for Claude's response.
  // We use this to show a loading indicator and disable the send button.
  const [loading, setLoading] = useState(false);

  // --- Refs ------------------------------------------------------------------
  // messagesEndRef: a dummy div at the bottom of the messages area.
  // We scroll this into view whenever a new message is added, which keeps
  // the chat auto-scrolled to the latest message.
  const messagesEndRef = useRef(null);

  // inputRef: reference to the text input so we can auto-focus it
  const inputRef = useRef(null);

  // --- Auto-scroll to bottom when messages change ----------------------------
  useEffect(() => {
    // scrollIntoView with "smooth" gives a nice animated scroll effect
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]); // trigger on new messages OR loading state change

  // --- Send a message to the backend -----------------------------------------
  const sendMessage = async () => {
    // Guard: don't send empty messages or send while already loading
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    // 1. Add the user's message to the chat immediately (optimistic UI).
    //    We don't wait for the API — the user sees their message right away.
    const userMessage = {
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);

    // 2. Clear the input field so the user can start typing their next message
    setInput("");

    // 3. Show the loading indicator
    setLoading(true);

    try {
      // 4. Send the message to our FastAPI backend.
      //    POST /api/chat with { message, session_id }
      //    The backend forwards this to the Anthropic API and returns
      //    Claude's response.
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: "demo-session", // hardcoded for now — Redis will use this later
        }),
      });

      // 5. Parse the JSON response from our backend
      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }
      const data = await response.json();

      // 6. Add Claude's response to the chat.
      //    We store the usage data too so we can display token counts.
      const assistantMessage = {
        role: "assistant",
        content: data.response,
        timestamp: new Date().toISOString(),
        usage: data.usage, // { input_tokens, output_tokens }
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      // If the API call fails, show the error as an assistant message
      // so the user can see what went wrong without opening dev tools.
      const errorMessage = {
        role: "assistant",
        content: `Error: ${error.message}. Is the backend running on port 8000?`,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      // 7. Hide loading indicator and refocus the input
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  // --- Handle Enter key to send ----------------------------------------------
  const handleKeyDown = (e) => {
    // Send on Enter, but not on Shift+Enter (which should add a newline
    // if we ever switch to a textarea)
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); // prevent form submission / newline
      sendMessage();
    }
  };

  // --- Render ----------------------------------------------------------------
  return (
    <div className="app">
      {/* --- Header --------------------------------------------------------- */}
      <header className="header">
        <div className="header-dot" />
        <h1>Redis DevRel Content Strategist</h1>
        <p>Powered by Claude Haiku 4.5</p>
      </header>

      {/* --- Messages area -------------------------------------------------- */}
      <div className="messages">
        {/* If no messages yet, show a welcome prompt */}
        {messages.length === 0 && !loading && (
          <div className="empty-state">
            <h2>What should we create next?</h2>
            <p>
              I'm your DevRel content strategist. Tell me about recent launches,
              audience feedback, or upcoming events — and I'll help brainstorm
              content ideas and plan your editorial roadmap.
            </p>
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
                    {msg.usage.input_tokens} input / {msg.usage.output_tokens} output
                    {" "}tokens
                  </span>
                )}
              </div>
            </div>
          </div>
          );
        })}

        {/* Loading indicator — three pulsing dots */}
        {loading && (
          <div className="loading">
            <div className="loading-dot" />
            <div className="loading-dot" />
            <div className="loading-dot" />
          </div>
        )}

        {/* Invisible anchor for auto-scroll */}
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
        <button onClick={sendMessage} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

export default App;
