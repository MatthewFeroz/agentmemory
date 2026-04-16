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
// Talks to our FastAPI backend through the /api prefix by default.
// A custom VITE_API_URL can still override that when needed.
// =============================================================================

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// --- Configuration -----------------------------------------------------------
const API_URL =
  import.meta.env.VITE_API_URL || "/api";
const LONG_TERM_USER_ID =
  import.meta.env.VITE_LONG_TERM_USER_ID || "default-user";

async function buildResponseError(response) {
  const baseMessage = `Server error: ${response.status}`;
  const contentType = response.headers.get("content-type") || "";

  try {
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      const detail =
        typeof payload?.detail === "string"
          ? payload.detail
          : typeof payload?.message === "string"
            ? payload.message
            : "";

      return detail ? `${baseMessage}. ${detail}` : baseMessage;
    }

    const text = (await response.text()).trim();
    return text ? `${baseMessage}. ${text}` : baseMessage;
  } catch {
    return baseMessage;
  }
}

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
    suggestions: [
      "Help me brainstorm a LinkedIn post about Redis for AI apps.",
      "Write me 5 hooks for a Redis developer tutorial.",
      "Give me 3 blog ideas for backend engineers learning Redis.",
    ],
  },
  {
    id: "short-term",
    label: "Short-Term Memory",
    themeClass: "theme-short-term",
    heading: "Short-Term Memory",
    description:
      "The assistant remembers everything from this session. Conversation history is loaded from Redis on every request.",
    session: "demo-short-term",
    suggestions: [
      "Help me outline a Redis webinar for software engineers.",
      "Write me 5 hooks for a post about Redis caching strategies.",
      "Turn these ideas into a short content plan for next month.",
    ],
  },
  {
    id: "long-term",
    label: "Long-Term Memory",
    themeClass: "theme-long-term",
    heading: "Long-Term Memory",
    description:
      "The assistant remembers you across sessions. Facts and preferences persist in Redis even after you start a new conversation.",
    session: "demo-long-term",
    suggestions: [
      "Remember that our audience prefers hands-on Redis tutorials.",
      "Brainstorm a LinkedIn post for Redis developers using that audience context.",
      "Write me 5 hooks related to Redis, software performance, and AI.",
    ],
  },
];

// Extraction mode options surfaced in long-term mode. Each entry carries a
// short label for the control and a one-line explainer for the audience.
// The 'id' field is what the backend expects on ChatRequest.extraction_mode.
const EXTRACTION_MODES = [
  {
    id: "regex",
    label: "Regex",
    description:
      "Deterministic pattern match only. Captures ~7 phrasings instantly.",
    hint: 'Try: "My name is Matthew." — saves instantly.',
  },
  {
    id: "ams",
    label: "AMS",
    description:
      "LLM-backed discrete extraction only. Async, catches flexible phrasings.",
    hint: "Describe any fact in your own words — AMS extracts it in ~10s.",
  },
  {
    id: "both",
    label: "Both",
    description: "Regex plus AMS discrete extraction (default).",
    hint: "Either path can fire — mix a fixed pattern with a free-form fact.",
  },
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

function formatFactDate(timestamp) {
  if (!timestamp) return null;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingLongTermArchive, setLoadingLongTermArchive] = useState(false);
  const [loadingRememberedFacts, setLoadingRememberedFacts] = useState(false);
  const [rememberedFacts, setRememberedFacts] = useState([]);
  const [rememberedFactsError, setRememberedFactsError] = useState("");
  const [deletingFactIds, setDeletingFactIds] = useState(new Set());
  const [longTermChats, setLongTermChats] = useState(() => [
    createDraftLongTermChat(),
  ]);
  const [activeLongTermChatId, setActiveLongTermChatId] = useState(
    () => longTermChats[0].id
  );
  const [activeMode, setActiveMode] = useState(0);
  const [extractionMode, setExtractionMode] = useState("both");

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
  const loadRememberedFactsRef = useRef(async () => {});
  const factsPollTimeoutsRef = useRef([]);

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
        throw new Error(await buildResponseError(response));
      }

      const data = await response.json();
      upsertLongTermChat(sessionId, (existing) => {
        // The archive endpoint returns role/content/timestamp but not
        // the per-response metadata (memoryContext, usage) that
        // sendMessage() attaches locally. Merge by index so reloading
        // the transcript from the archive doesn't discard that metadata.
        const mergedMessages = (data.messages || []).map((archivedMsg, i) => {
          const existingMsg = existing.messages?.[i];
          if (existingMsg && existingMsg.role === archivedMsg.role) {
            return {
              ...archivedMsg,
              memoryContext: existingMsg.memoryContext || null,
              usage: existingMsg.usage || null,
            };
          }
          return archivedMsg;
        });

        return {
          ...existing,
          id: data.session_id,
          sessionId: data.session_id,
          label: data.label,
          messages: mergedMessages,
          isDraft: false,
          hasLoadedMessages: true,
        };
      });
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
        throw new Error(await buildResponseError(response));
      }

      const data = await response.json();
      const archiveChats = data.chats || [];

      // Use a functional updater so we read the latest pending state.
      // Without this, sendMessage's appendMessage (which adds memoryContext
      // and usage to messages) would be overwritten by a stale closure
      // reading the old longTermChats value.
      let computedNextChats = [];
      setLongTermChats((prev) => {
        const drafts = prev.filter(
          (chat) =>
            chat.isDraft &&
            !archiveChats.some((archiveChat) => archiveChat.session_id === chat.sessionId)
        );
        const previousBySessionId = new Map(
          prev.map((chat) => [chat.sessionId, chat])
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

        computedNextChats =
          drafts.length || mergedChats.length
            ? [...drafts, ...mergedChats]
            : [createDraftLongTermChat()];

        return computedNextChats;
      });

      const nextActiveId =
        preferredSessionId ||
        (computedNextChats.some((chat) => chat.id === activeLongTermChatId)
          ? activeLongTermChatId
          : computedNextChats[0].id);

      setActiveLongTermChatId(nextActiveId);

      const selectedChat = computedNextChats.find((chat) => chat.id === nextActiveId);
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

  const loadRememberedFacts = async () => {
    setLoadingRememberedFacts(true);
    setRememberedFactsError("");

    try {
      const response = await fetch(
        `${API_URL}/long-term/facts?user_id=${encodeURIComponent(LONG_TERM_USER_ID)}`
      );

      if (!response.ok) {
        throw new Error(await buildResponseError(response));
      }

      const data = await response.json();
      setRememberedFacts(data.facts || []);
    } catch (error) {
      setRememberedFacts([]);
      setRememberedFactsError(error.message);
    } finally {
      setLoadingRememberedFacts(false);
    }
  };

  loadRememberedFactsRef.current = loadRememberedFacts;

  const cancelFactsPoll = () => {
    factsPollTimeoutsRef.current.forEach((id) => clearTimeout(id));
    factsPollTimeoutsRef.current = [];
  };

  // Schedules repeated facts refetches to surface async AMS extractions
  // without waiting for the next turn. Defaults target ~30s of 2s-interval
  // polling, which comfortably covers typical worker latency.
  const scheduleFactsPoll = (intervalMs = 2000, durationMs = 30000) => {
    cancelFactsPoll();
    const attempts = Math.floor(durationMs / intervalMs);
    for (let i = 1; i <= attempts; i += 1) {
      const id = setTimeout(() => {
        void loadRememberedFactsRef.current();
      }, i * intervalMs);
      factsPollTimeoutsRef.current.push(id);
    }
  };

  const deleteFact = async (factId) => {
    if (!factId || deletingFactIds.has(factId)) return;

    setDeletingFactIds((prev) => new Set(prev).add(factId));

    try {
      const response = await fetch(`${API_URL}/long-term/facts`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memory_ids: [factId] }),
      });

      if (!response.ok) {
        throw new Error(await buildResponseError(response));
      }

      // Remove the fact from local state immediately for snappy UX,
      // then refresh from the server to stay consistent.
      setRememberedFacts((prev) => prev.filter((f) => f.id !== factId));
      await loadRememberedFacts();
    } catch (error) {
      setRememberedFactsError(`Delete failed: ${error.message}`);
    } finally {
      setDeletingFactIds((prev) => {
        const next = new Set(prev);
        next.delete(factId);
        return next;
      });
    }
  };

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
          extraction_mode:
            requestModeId === "long-term" ? extractionMode : undefined,
        }),
      });

      if (!response.ok) {
        throw new Error(await buildResponseError(response));
      }
      const data = await response.json();

      const assistantMessage = {
        role: "assistant",
        content: data.response,
        timestamp: new Date().toISOString(),
        usage: data.usage,
        // Attach memory context from the backend so we can render what
        // memory was used to produce this specific response. Each message
        // carries its own snapshot because the counts change over time
        // (e.g. messages_loaded grows with each exchange in a session).
        memoryContext: data.memory_context || null,
      };
      appendMessage(assistantMessage, {
        targetModeId: requestModeId,
        targetLongTermSessionId: requestSessionId,
      });

      if (requestModeId === "long-term") {
        await loadLongTermChats(requestSessionId);
        await loadLongTermChat(requestSessionId);
        await loadRememberedFactsRef.current();

        // AMS discrete extraction runs asynchronously in the worker. Poll
        // repeatedly so background-extracted records appear in the panel
        // without the user having to send another message.
        if (extractionMode === "ams" || extractionMode === "both") {
          scheduleFactsPoll();
        }
      }
    } catch (error) {
      const errorMessage = {
        role: "assistant",
        content: `Error: ${error.message}. Check that the backend and memory services are reachable.`,
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
    if (mode.id !== "long-term") {
      cancelFactsPoll();
      return;
    }
    void loadLongTermChatsRef.current();
    void loadRememberedFactsRef.current();
    return () => cancelFactsPoll();
  }, [mode.id]);

  const savedLongTermChatCount = longTermChats.filter((chat) => !chat.isDraft).length;

  return (
    <div className="app">
      <header className="header">
        <div className="header-dot" />
        <h1>
          <span className="header-redis">Redis</span> AI Content Strategist
        </h1>
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

      <div className={`chat-body${mode.id === "long-term" ? " has-sidebar" : ""}`}>
      <div className="messages">
        {displayedMessages.length === 0 && !loading && !loadingLongTermArchive && (
          <div className="empty-state">
            <h2>{mode.heading}</h2>
            <p>{mode.description}</p>
            <div className="suggestion-chips">
              {mode.suggestions.map((text) => (
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
                  {/* Memory context: shows what memory was loaded for this
                      response, inline with timestamp and token usage. Uses the
                      condensed format so it doesn't overwhelm the message. */}
                  {msg.role === "assistant" && msg.memoryContext && (
                    <span className="memory-context">
                      {msg.memoryContext.memory_mode === "none" && "no memory"}
                      {msg.memoryContext.memory_mode === "short-term" &&
                        `${msg.memoryContext.messages_loaded} prior messages loaded`}
                      {msg.memoryContext.memory_mode === "long-term" &&
                        `${msg.memoryContext.messages_loaded} prior messages + ${msg.memoryContext.long_term_memories_retrieved} long-term memories loaded`}
                      {msg.memoryContext.extraction_mode &&
                        ` · extraction: ${msg.memoryContext.extraction_mode}`}
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

      {mode.id === "long-term" && (
        <aside className="sidebar">
          <div className="sidebar-section">
            <span className="sidebar-label">Extraction</span>
            <div
              className="mode-switcher extraction-mode-switcher"
              role="group"
              aria-label="Extraction mode"
            >
              {EXTRACTION_MODES.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={`mode-btn${extractionMode === option.id ? " active" : ""}`}
                  onClick={() => setExtractionMode(option.id)}
                  title={option.description}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="extraction-mode-hint">
              {
                EXTRACTION_MODES.find((option) => option.id === extractionMode)
                  ?.hint
              }
            </p>
          </div>

          <div className="sidebar-section">
            <span className="sidebar-label">Chat Archive</span>
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
            <div className="sidebar-row">
              <span className="sidebar-muted">
                {loadingLongTermArchive
                  ? "Syncing..."
                  : `${savedLongTermChatCount} chats saved`}
              </span>
              <button
                type="button"
                className="new-chat-btn"
                onClick={startNewLongTermChat}
                disabled={loading || loadingLongTermArchive}
              >
                New Chat
              </button>
            </div>
          </div>

          <div className="sidebar-section">
            <div className="sidebar-row">
              <span className="sidebar-label">Remembered Facts</span>
              <div className="facts-header-actions">
                <span className="sidebar-muted">
                  {loadingRememberedFacts
                    ? "loading..."
                    : `${rememberedFacts.length}`}
                </span>
                <button
                  type="button"
                  className="facts-refresh-btn"
                  onClick={() => void loadRememberedFactsRef.current()}
                  disabled={loadingRememberedFacts}
                  title="Re-fetch facts from AMS"
                  aria-label="Refresh remembered facts"
                >
                  Refresh
                </button>
              </div>
            </div>

            {rememberedFactsError && (
              <p className="sidebar-muted">Unable to load facts.</p>
            )}

            {!rememberedFactsError && rememberedFacts.length === 0 && !loadingRememberedFacts && (
              <p className="sidebar-empty">
                No facts yet. Try "My name is Matthew" or "I visited RedisConf on April 10, 2026."
              </p>
            )}

            {rememberedFacts.length > 0 && (
              <ul className="fact-list">
                {rememberedFacts.map((fact, index) => {
                  const isDeleting = fact.id && deletingFactIds.has(fact.id);
                  // Origin topics are tagged by the writer: "demo-seed" by the
                  // seed script, "demo-regex" by the backend pattern extractor.
                  // Everything else is treated as AMS discrete-extraction origin.
                  const topics = fact.topics || [];
                  const isSeedOrigin = topics.includes("demo-seed");
                  const isRegexOrigin = !isSeedOrigin && topics.includes("demo-regex");
                  const originVariant = isSeedOrigin
                    ? "seed"
                    : isRegexOrigin
                    ? "regex"
                    : "ams";
                  const originLabel = isSeedOrigin
                    ? "seeded"
                    : isRegexOrigin
                    ? "regex"
                    : "AMS";
                  const originTooltip = isSeedOrigin
                    ? "Pre-loaded by the seed script before the demo"
                    : isRegexOrigin
                    ? "Extracted by the deterministic regex layer"
                    : "Extracted by AMS discrete strategy";
                  return (
                    <li
                      key={fact.id || `${fact.text}-${fact.source_session_id || index}`}
                      className={`fact-item${isDeleting ? " fact-item--deleting" : ""}`}
                    >
                      <div className="fact-header">
                        <p className="fact-text">{fact.text}</p>
                        {fact.id && (
                          <button
                            type="button"
                            className="fact-delete-btn"
                            title="Forget this fact"
                            disabled={isDeleting}
                            onClick={() => deleteFact(fact.id)}
                            aria-label={`Delete fact: ${fact.text}`}
                          >
                            &times;
                          </button>
                        )}
                      </div>
                      <div className="fact-meta">
                        <span
                          className={`fact-origin-badge ${originVariant}`}
                          title={originTooltip}
                        >
                          {isSeedOrigin ? originLabel : `via ${originLabel}`}
                        </span>
                        {fact.memory_type && (
                          <span className={`fact-badge ${fact.memory_type}`}>
                            {fact.memory_type}
                          </span>
                        )}
                        {fact.event_date && (
                          <span className="fact-date">
                            {formatFactDate(fact.event_date)}
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </aside>
      )}
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
