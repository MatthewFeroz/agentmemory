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
// Talks to our FastAPI backend. In development it defaults to localhost:8000;
// in production it can still use the /api prefix if a reverse proxy is present.
// =============================================================================

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// --- Configuration -----------------------------------------------------------
const API_URL =
  import.meta.env.VITE_API_URL ||
  (import.meta.env.DEV ? "http://localhost:8000" : "/api");
const LONG_TERM_USER_ID =
  import.meta.env.VITE_LONG_TERM_USER_ID || "demo-long-term-user";

// --- Mode definitions --------------------------------------------------------
const MODES = [
  {
    id: "none",
    label: "No Memory",
    themeClass: "",
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

// --- Suggestion chips --------------------------------------------------------
const SUGGESTIONS = [
  "Template prompt one",
  "Template prompt two",
  "Template prompt three",
];

function createSessionId(prefix) {
  return `${prefix}-${Date.now()}`;
}

function createDraftLongTermChat(sessionId = createSessionId("demo-long-term")) {
  return {
    id: sessionId,
    label: "New Chat",
    sessionId,
    messages: [],
    isDraft: true,
    hasLoadedMessages: true,
    preview: null,
    lastUpdated: null,
    messageCount: 0,
  };
}

function formatMessageTime(timestamp) {
  if (!timestamp) return null;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingLongTermArchive, setLoadingLongTermArchive] = useState(false);
  const [longTermChats, setLongTermChats] = useState(() => [
    createDraftLongTermChat("demo-long-term"),
  ]);
  const [activeLongTermChatId, setActiveLongTermChatId] = useState("demo-long-term");
  const [activeMode, setActiveMode] = useState(0);

  const mode = MODES[activeMode];
  const activeLongTermChat =
    longTermChats.find((chat) => chat.id === activeLongTermChatId) || longTermChats[0];
  const displayedMessages =
    mode.id === "long-term" ? (activeLongTermChat?.messages || []) : messages;
  const currentSessionId =
    mode.id === "long-term" ? activeLongTermChat?.sessionId : mode.session;

  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const loadLongTermChatsRef = useRef(async () => {});

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, longTermChats, loading, loadingLongTermArchive]);

  useEffect(() => {
    document.body.classList.remove("theme-short-term", "theme-long-term");
    if (mode.themeClass) {
      document.body.classList.add(mode.themeClass);
    }
  }, [mode.themeClass]);

  const switchMode = (index) => {
    if (index === activeMode) return;
    setActiveMode(index);
    setInput("");

    if (MODES[index].id !== "long-term") {
      setMessages([]);
    }
  };

  const upsertLongTermChat = (sessionId, updater) => {
    setLongTermChats((prev) => {
      const existing = prev.find((chat) => chat.sessionId === sessionId);
      const updated = updater(existing || createDraftLongTermChat(sessionId));

      if (existing) {
        return prev.map((chat) =>
          chat.sessionId === sessionId ? updated : chat
        );
      }

      return [updated, ...prev];
    });
  };

  const loadLongTermChat = async (sessionId) => {
    if (!sessionId) return;

    setLoadingLongTermArchive(true);
    try {
      const response = await fetch(
        `${API_URL}/long-term/chats/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(LONG_TERM_USER_ID)}`
      );

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }

      const data = await response.json();
      upsertLongTermChat(sessionId, (existing) => ({
        ...existing,
        id: data.session_id,
        sessionId: data.session_id,
        label: data.label,
        messages: data.messages,
        isDraft: false,
        hasLoadedMessages: true,
      }));
      setActiveLongTermChatId(data.session_id);
    } catch (error) {
      upsertLongTermChat(sessionId, (existing) => ({
        ...existing,
        messages: [
          {
            role: "assistant",
            content: `Error loading archived chat: ${error.message}`,
            timestamp: new Date().toISOString(),
          },
        ],
        hasLoadedMessages: true,
      }));
    } finally {
      setLoadingLongTermArchive(false);
      inputRef.current?.focus();
    }
  };

  const loadLongTermChats = async (preferredSessionId = null) => {
    setLoadingLongTermArchive(true);
    try {
      const response = await fetch(
        `${API_URL}/long-term/chats?user_id=${encodeURIComponent(LONG_TERM_USER_ID)}`
      );

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }

      const data = await response.json();
      const archiveChats = data.chats || [];
      const drafts = longTermChats.filter(
        (chat) =>
          chat.isDraft &&
          !archiveChats.some((archiveChat) => archiveChat.session_id === chat.sessionId)
      );
      const previousBySessionId = new Map(
        longTermChats.map((chat) => [chat.sessionId, chat])
      );

      const mergedChats = archiveChats.map((chat) => {
        const existing = previousBySessionId.get(chat.session_id);
        return {
          id: chat.session_id,
          sessionId: chat.session_id,
          label: chat.label,
          messages: existing?.messages || [],
          isDraft: false,
          hasLoadedMessages: existing?.hasLoadedMessages || false,
          preview: chat.preview,
          lastUpdated: chat.last_updated,
          messageCount: chat.message_count,
        };
      });

      const nextChats =
        drafts.length || mergedChats.length
          ? [...drafts, ...mergedChats]
          : [createDraftLongTermChat()];

      setLongTermChats(nextChats);

      const nextActiveId =
        preferredSessionId ||
        (nextChats.some((chat) => chat.id === activeLongTermChatId)
          ? activeLongTermChatId
          : nextChats[0].id);

      setActiveLongTermChatId(nextActiveId);

      const selectedChat = nextChats.find((chat) => chat.id === nextActiveId);
      if (selectedChat && !selectedChat.isDraft && !selectedChat.hasLoadedMessages) {
        await loadLongTermChat(selectedChat.sessionId);
      }
    } catch {
      setLongTermChats((prev) =>
        prev.length > 0 ? prev : [createDraftLongTermChat()]
      );
    } finally {
      setLoadingLongTermArchive(false);
    }
  };

  loadLongTermChatsRef.current = loadLongTermChats;

  const startNewLongTermChat = () => {
    const draftChat = createDraftLongTermChat();
    setLongTermChats((prev) => [draftChat, ...prev]);
    setActiveLongTermChatId(draftChat.id);
    setInput("");
    inputRef.current?.focus();
  };

  const selectLongTermChat = async (event) => {
    const nextChatId = event.target.value;
    setActiveLongTermChatId(nextChatId);
    setInput("");

    const selectedChat = longTermChats.find((chat) => chat.id === nextChatId);
    if (selectedChat && !selectedChat.isDraft && !selectedChat.hasLoadedMessages) {
      await loadLongTermChat(selectedChat.sessionId);
    }

    inputRef.current?.focus();
  };

  const appendMessage = (message, options = {}) => {
    const {
      targetModeId = mode.id,
      targetLongTermSessionId = currentSessionId,
    } = options;

    if (targetModeId === "long-term") {
      upsertLongTermChat(targetLongTermSessionId, (existing) => ({
        ...existing,
        id: targetLongTermSessionId,
        sessionId: targetLongTermSessionId,
        messages: [...(existing.messages || []), message],
        hasLoadedMessages: true,
      }));
      return;
    }

    setMessages((prev) => [...prev, message]);
  };

  const sendMessage = async (overrideText) => {
    const trimmed = (overrideText || input).trim();
    if (!trimmed || loading || !currentSessionId) return;

    const requestModeId = mode.id;
    const requestSessionId = currentSessionId;

    const userMessage = {
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };
    appendMessage(userMessage, {
      targetModeId: requestModeId,
      targetLongTermSessionId: requestSessionId,
    });
    setInput("");
    setLoading(true);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: requestSessionId,
          memory_mode: requestModeId,
          user_id: requestModeId === "long-term" ? LONG_TERM_USER_ID : undefined,
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
      appendMessage(assistantMessage, {
        targetModeId: requestModeId,
        targetLongTermSessionId: requestSessionId,
      });

      if (requestModeId === "long-term") {
        await loadLongTermChats(requestSessionId);
        await loadLongTermChat(requestSessionId);
      }
    } catch (error) {
      const errorMessage = {
        role: "assistant",
        content: `Error: ${error.message}. Is the backend running on port 8000?`,
        timestamp: new Date().toISOString(),
      };
      appendMessage(errorMessage, {
        targetModeId: requestModeId,
        targetLongTermSessionId: requestSessionId,
      });
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  useEffect(() => {
    if (mode.id !== "long-term") return;
    void loadLongTermChatsRef.current();
  }, [mode.id]);

  const savedLongTermChatCount = longTermChats.filter((chat) => !chat.isDraft).length;

  return (
    <div className="app">
      <header className="header">
        <div className="header-dot" />
        <h1>
          <span className="header-redis">Redis</span> DevRel
        </h1>
        <span className="header-subtitle">AI Content Strategist</span>
      </header>

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

      <div className="messages">
        {mode.id === "long-term" && (
          <div className="conversation-toolbar">
            <div className="conversation-details">
              <span className="conversation-label">Chat Archive</span>
              <div className="conversation-picker-wrap">
                <label className="sr-only" htmlFor="long-term-chat-picker">
                  Select a previous long-term chat
                </label>
                <select
                  id="long-term-chat-picker"
                  className="conversation-picker"
                  value={activeLongTermChatId || ""}
                  onChange={selectLongTermChat}
                  disabled={loading || loadingLongTermArchive}
                >
                  {longTermChats.map((chat) => (
                    <option key={chat.id} value={chat.id}>
                      {chat.label}
                    </option>
                  ))}
                </select>
                <span className="conversation-value">
                  {loadingLongTermArchive
                    ? "Syncing archive..."
                    : `${savedLongTermChatCount} saved conversation${
                        savedLongTermChatCount === 1 ? "" : "s"
                      }`}
                </span>
              </div>
            </div>
            <button
              type="button"
              className="new-chat-btn"
              onClick={startNewLongTermChat}
              disabled={loading || loadingLongTermArchive}
            >
              New Chat
            </button>
          </div>
        )}

        {displayedMessages.length === 0 && !loading && !loadingLongTermArchive && (
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

        {displayedMessages.map((msg, i) => {
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

        {(loading || loadingLongTermArchive) && (
          <div className="loading">
            <div className="loading-dot" />
            <div className="loading-dot" />
            <div className="loading-dot" />
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="input-area">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about content ideas, editorial planning, audience strategy..."
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
