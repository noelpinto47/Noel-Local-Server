const messages = [];
let conversationId = localStorage.getItem("conversation_id");
let isSending = false;
let conversations = [];

const savedTheme = localStorage.getItem("ai-mind-theme") || "dark";
document.body.classList.toggle("light", savedTheme === "light");

const themeToggle = document.getElementById("theme-toggle");
const mobileHistoryButton = document.getElementById("mobile-history");
const historyBackdrop = document.getElementById("history-backdrop");
const appShell = document.querySelector(".app-shell");
const historySearch = document.getElementById("history-search");
const conversationList = document.getElementById("conversation-list");
const historyCount = document.getElementById("history-count");
const chat = document.getElementById("chat");
const messagesContainer = document.getElementById("messages");
const welcome = document.getElementById("welcome");
const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const newChatButton = document.getElementById("new-chat");
const chatTitle = document.getElementById("chat-title");

marked.setOptions({gfm: true, breaks: true});

function updateThemeToggle() {
  const light = document.body.classList.contains("light");
  themeToggle.textContent = light ? "☾" : "☼";
  themeToggle.title = light ? "Switch to dark mode" : "Switch to light mode";
  themeToggle.setAttribute("aria-label", themeToggle.title);
}

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || ""), {USE_PROFILES: {html: true}});
}

function escapeHtml(value) {
  const element = document.createElement("div");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function formatConversationDate(value) {
  if (!value) return "";
  const date = new Date(`${value.replace(" ", "T")}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString([], {month: "short", day: "numeric"});
}

function scrollToBottom() {
  requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
}

function setWelcomeVisible(visible) {
  welcome.style.display = visible ? "" : "none";
}

function clearMessages() {
  messages.length = 0;
  messagesContainer.innerHTML = "";
  setWelcomeVisible(true);
}

function addMessage(role, content, save = true) {
  if (save) messages.push({role, content});
  setWelcomeVisible(false);

  const message = document.createElement("div");
  message.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "N" : "✦";
  const box = document.createElement("div");
  box.className = "message-content";
  const markdown = document.createElement("div");
  markdown.className = "markdown";
  markdown.innerHTML = renderMarkdown(content);
  box.appendChild(markdown);
  message.append(avatar, box);
  messagesContainer.appendChild(message);
  scrollToBottom();
}

function showTyping() {
  const message = document.createElement("div");
  message.className = "message assistant";
  message.id = "typing-message";
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = "✦";
  const box = document.createElement("div");
  box.className = "message-content";
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.innerHTML = "<span></span><span></span><span></span>";
  box.appendChild(typing);
  message.append(avatar, box);
  messagesContainer.appendChild(message);
  scrollToBottom();
}

function hideTyping() {
  document.getElementById("typing-message")?.remove();
}

function setSending(value) {
  isSending = value;
  sendButton.disabled = value;
  input.disabled = value;
}

function autoResize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function setHistoryOpen(open) {
  appShell.classList.toggle("history-open", open);
  mobileHistoryButton.setAttribute("aria-expanded", String(open));
}

function renderConversations() {
  conversationList.innerHTML = "";
  historyCount.textContent = conversations.length ? String(conversations.length) : "";

  if (!conversations.length) {
    const empty = document.createElement("div");
    empty.className = "conversation-empty";
    empty.textContent = historySearch.value.trim() ? "No matching chats" : "No previous chats yet";
    conversationList.appendChild(empty);
    return;
  }

  conversations.forEach(conversation => {
    const item = document.createElement("div");
    item.tabIndex = 0;
    item.className = `conversation-item${conversation.id === conversationId ? " active" : ""}`;
    item.title = conversation.title;

    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = conversation.title || "New conversation";
    const preview = document.createElement("span");
    preview.className = "conversation-preview";
    preview.textContent = conversation.preview || "No messages yet";
    const meta = document.createElement("span");
    meta.className = "conversation-meta";
    meta.textContent = `${formatConversationDate(conversation.updated_at)} · ${conversation.message_count} messages`;

    const actions = document.createElement("span");
    actions.className = "conversation-actions";
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "conversation-action";
    rename.title = "Rename chat";
    rename.setAttribute("aria-label", `Rename ${conversation.title}`);
    rename.textContent = "✎";
    rename.onclick = event => {
      event.stopPropagation();
      renameConversation(conversation);
    };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "conversation-action delete";
    remove.title = "Delete chat";
    remove.setAttribute("aria-label", `Delete ${conversation.title}`);
    remove.textContent = "×";
    remove.onclick = event => {
      event.stopPropagation();
      removeConversation(conversation);
    };
    actions.append(rename, remove);
    item.append(title, preview, meta, actions);
    item.onclick = () => selectConversation(conversation.id);
    item.onkeydown = event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectConversation(conversation.id);
      }
    };
    conversationList.appendChild(item);
  });
}

async function loadConversations() {
  const params = new URLSearchParams({
    search: historySearch.value.trim()
  });

  try {
    const response = await fetch(`/api/conversations?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load chat history.");
    conversations = data.conversations || [];
    renderConversations();
    return conversations;
  } catch (error) {
    conversationList.innerHTML = `<div class="conversation-empty">${escapeHtml(error.message)}</div>`;
    return [];
  }
}

async function selectConversation(id) {
  if (isSending) return;
  if (id === conversationId && messages.length) {
    setHistoryOpen(false);
    return;
  }

  try {
    const response = await fetch(`/api/conversation/${id}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Conversation not found.");
    conversationId = id;
    localStorage.setItem("conversation_id", id);
    messages.length = 0;
    messagesContainer.innerHTML = "";
    (data.messages || []).forEach(message => addMessage(message.role, message.content));
    chatTitle.textContent = data.title || "New conversation";
    setWelcomeVisible(!(data.messages || []).length);
    renderConversations();
    setHistoryOpen(false);
  } catch (error) {
    localStorage.removeItem("conversation_id");
    conversationId = null;
    clearMessages();
    chatTitle.textContent = "New conversation";
    await loadConversations();
  }
}

async function renameConversation(conversation) {
  const title = window.prompt("Rename chat", conversation.title);
  if (title === null) return;
  const cleanedTitle = title.trim();
  if (!cleanedTitle) return;

  const response = await fetch(`/api/conversations/${conversation.id}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({title: cleanedTitle})
  });
  const data = await response.json();
  if (!response.ok) {
    window.alert(data.error || "Could not rename chat.");
    return;
  }
  if (conversation.id === conversationId) chatTitle.textContent = data.conversation.title;
  await loadConversations();
}

async function removeConversation(conversation) {
  if (!window.confirm(`Delete "${conversation.title}"? This cannot be undone.`)) return;

  const response = await fetch(`/api/conversations/${conversation.id}`, {method: "DELETE"});
  const data = await response.json();
  if (!response.ok) {
    window.alert(data.error || "Could not delete chat.");
    return;
  }

  if (conversation.id === conversationId) {
    conversationId = null;
    localStorage.removeItem("conversation_id");
    clearMessages();
    chatTitle.textContent = "New conversation";
  }
  await loadConversations();
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || isSending) return;
  addMessage("user", text);
  input.value = "";
  autoResize();
  setSending(true);
  showTyping();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, conversation_id: conversationId})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The server could not process the request.");
    if (data.conversation_id) {
      conversationId = data.conversation_id;
      localStorage.setItem("conversation_id", conversationId);
    }
    if (!data.response) throw new Error("The AI returned an empty response.");
    hideTyping();
    addMessage("assistant", data.response);
    chatTitle.textContent = text.length > 42 ? `${text.slice(0, 42)}…` : text;
    await loadConversations();
  } catch (error) {
    hideTyping();
    addMessage("assistant", `**Something went wrong.**\n\n${error.message}`);
    messages.pop();
  } finally {
    setSending(false);
    input.focus();
  }
}

async function loadConversation() {
  clearMessages();
  const history = await loadConversations();
  const active = history.find(conversation => conversation.id === conversationId);
  if (active) {
    await selectConversation(active.id);
  } else {
    localStorage.removeItem("conversation_id");
    conversationId = null;
  }
}

themeToggle.addEventListener("click", () => {
  const light = !document.body.classList.contains("light");
  document.body.classList.toggle("light", light);
  localStorage.setItem("ai-mind-theme", light ? "light" : "dark");
  updateThemeToggle();
});
newChatButton.addEventListener("click", () => {
  localStorage.removeItem("conversation_id");
  conversationId = null;
  clearMessages();
  chatTitle.textContent = "New conversation";
  renderConversations();
  setHistoryOpen(false);
  input.value = "";
  autoResize();
  input.focus();
});
mobileHistoryButton.addEventListener("click", () => setHistoryOpen(true));
historyBackdrop.addEventListener("click", () => setHistoryOpen(false));
historySearch.addEventListener("input", loadConversations);
form.addEventListener("submit", event => { event.preventDefault(); sendMessage(input.value); });
input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener("input", autoResize);
document.querySelectorAll(".suggestion").forEach(button => button.addEventListener("click", () => sendMessage(button.dataset.prompt)));

updateThemeToggle();
loadConversation();
input.focus();
