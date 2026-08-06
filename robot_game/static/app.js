const select = (selector) => document.querySelector(selector);
const selectAll = (selector) => [...document.querySelectorAll(selector)];

const INITIAL_STATE = {
  stage: "NO SESSION",
  round_id: null,
};

let websocket;
let state = { ...INITIAL_STATE };
let researcherToken = "";


function escapeHtml(value) {
  const entities = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };

  return String(value).replace(/[&<>"']/g, (character) => entities[character]);
}


function appendEventLog(message, isError = false) {
  const item = document.createElement("li");
  item.className = isError ? "error" : "";
  item.innerHTML = [
    `<time>${new Date().toLocaleTimeString()}</time>`,
    escapeHtml(message),
  ].join("");

  select("#events").prepend(item);
}


function showToast(message, isError = false) {
  const toast = select("#toast");
  toast.textContent = message;
  toast.className = `show${isError ? " error" : ""}`;

  window.setTimeout(() => {
    toast.className = "";
  }, 3500);
}


function updateLegalButtons() {
  selectAll("[data-stages]").forEach((button) => {
    const legalStages = button.dataset.stages.split(",");
    button.disabled = !legalStages.includes(state.stage);
  });
}


function renderState(payload) {
  // 服务端状态是唯一事实来源；按钮是否可用也随最新 stage 一起刷新。
  state = { ...state, ...payload };

  select("#stage").textContent = state.stage || "NO SESSION";
  select("#condition").textContent = state.condition || "—";
  select("#round").textContent = state.round_id || "—";
  select("#robotMode").textContent = [
    state.robot_mode || "—",
    state.robot_connected ? "connected" : "offline",
  ].join(" · ");
  select("#safetyMode").textContent = [
    state.robot_safety_mode || "unknown",
    state.robot_operating_mode || "unknown",
  ].join(" · ");
  select("#lastAction").textContent = state.current_action || "—";
  select("#endStatus").textContent = state.end_requested ? "Queued" : "Not queued";
  select("#wins").textContent = state.wins || 0;
  select("#losses").textContent = state.losses || 0;
  select("#participant").textContent = state.participant_id || "—";
  select("#session").textContent = state.session_id || "—";
  select("#configVersion").textContent = state.config_version || "—";
  select("#errorDetails").textContent = state.error || "None";

  select("#planDetails").textContent = state.last_action
    ? JSON.stringify(state.last_action, null, 2)
    : "No validated plan yet.";

  const isFormalStudy = state.formal_study === true;
  select("#pilotPanel").classList.toggle("hidden", isFormalStudy);

  updateLegalButtons();
}


function setConnectionStatus(isConnected) {
  select(".connection").classList.toggle("online", isConnected);
  select("#connectionText").textContent = isConnected
    ? "Connected"
    : "Disconnected";
}


function isErrorEvent(event) {
  return event.level === "ERROR" || event.level === "CRITICAL";
}


function handleSystemEvent(message) {
  const event = message.payload;
  const detail = JSON.stringify(event.data || {}).slice(0, 260);
  const summary = [
    `#${event.sequence}`,
    `[${event.category}]`,
    event.event,
    detail,
  ].join(" ");

  appendEventLog(summary, isErrorEvent(event));
}


function handleCommandAcknowledgement(message) {
  renderState(message.payload.state);
  appendEventLog(`ACK ${message.request_id}`);
}


function handleCommandError(message) {
  const errorMessage = message.payload.message;
  appendEventLog(`ERROR ${errorMessage}`, true);
  showToast(errorMessage, true);
}


function handleWebSocketMessage(rawMessage) {
  // 后端通过同一连接发送状态、实时事件和带 request_id 的命令结果。
  const message = JSON.parse(rawMessage.data);

  switch (message.type) {
    case "system.status":
      renderState(message.payload);
      break;
    case "system.event":
      handleSystemEvent(message);
      break;
    case "woz.ack":
      handleCommandAcknowledgement(message);
      break;
    case "woz.error":
      handleCommandError(message);
      break;
    default:
      appendEventLog(`Unknown server message: ${message.type}`, true);
  }
}


function buildWebSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const encodedToken = encodeURIComponent(researcherToken);
  return `${protocol}://${window.location.host}/ws?token=${encodedToken}`;
}


function connectWebSocket() {
  researcherToken = select("#token").value;
  websocket = new WebSocket(buildWebSocketUrl());

  websocket.onopen = () => {
    setConnectionStatus(true);
    appendEventLog("WebSocket connected");
  };

  websocket.onclose = (event) => {
    setConnectionStatus(false);
    appendEventLog(`WebSocket closed (${event.code})`, true);
  };

  websocket.onerror = () => {
    showToast("WebSocket connection failed", true);
  };

  websocket.onmessage = handleWebSocketMessage;
}


function buildWozCommand(command, payload) {
  // session_id/round_id 防止旧按钮事件影响其他会话或下一轮。
  return {
    type: "woz.command",
    session_id: state.session_id,
    round_id: state.round_id,
    timestamp: new Date().toISOString(),
    command,
    payload,
    request_id: crypto.randomUUID(),
  };
}


function sendCommand(command, payload = {}) {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) {
    showToast("WebSocket is not connected", true);
    return;
  }

  const message = buildWozCommand(command, payload);
  websocket.send(JSON.stringify(message));
  appendEventLog(`SEND ${command} · ${message.request_id.slice(0, 8)}`);
}


function getSessionFormData() {
  return {
    participant_id: select("#participantId").value,
    session_id: select("#sessionId").value,
    condition: select("#conditionInput").value,
    formal_study: select("#formalStudy").checked,
  };
}


async function startSession() {
  // REST 负责创建并锁定会话；创建成功后的实时控制继续走 WebSocket。
  researcherToken = select("#token").value;

  try {
    const response = await fetch("/api/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Researcher-Token": researcherToken,
      },
      body: JSON.stringify(getSessionFormData()),
    });
    const responseBody = await response.json();

    if (!response.ok) {
      throw new Error(responseBody.detail || "Unable to start session");
    }

    renderState(responseBody);
    showToast("Session started and condition locked");

    const socketIsClosingOrClosed =
      !websocket || websocket.readyState > WebSocket.OPEN;
    if (socketIsClosingOrClosed) {
      connectWebSocket();
    }
  } catch (error) {
    showToast(error.message, true);
    appendEventLog(error.message, true);
  }
}


function handleConfiguredCommand(button) {
  const confirmationMessage = button.dataset.confirm;
  if (confirmationMessage && !window.confirm(confirmationMessage)) {
    return;
  }

  const payload = JSON.parse(button.dataset.payload || "{}");
  sendCommand(button.dataset.command, payload);
}


function sendPilotAction() {
  sendCommand("pilot_action", {
    function: select("#pilotFunction").value,
  });
}


function sendUserResponseLabels() {
  const labels = selectAll("#responseTags input:checked").map(
    (input) => input.value,
  );

  if (labels.length === 0) {
    showToast("Select at least one label", true);
    return;
  }

  sendCommand("user_response", { labels });
}


function requestEmergencyStop() {
  const confirmed = window.confirm(
    "Issue an immediate software stop? Keep the physical E-stop accessible.",
  );
  if (!confirmed) {
    return;
  }

  sendCommand("emergency_stop", {
    reason: "operator_detected_risk",
  });
}


function bindEventHandlers() {
  select("#startSession").addEventListener("click", startSession);

  selectAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => handleConfiguredCommand(button));
  });

  select("#pilotAction").addEventListener("click", sendPilotAction);
  select("#sendLabels").addEventListener("click", sendUserResponseLabels);
  select("#emergencyStop").addEventListener("click", requestEmergencyStop);
  select("#clearLog").addEventListener("click", () => {
    select("#events").replaceChildren();
  });
}


function initializeConsole() {
  bindEventHandlers();
  connectWebSocket();
  updateLegalButtons();
}


initializeConsole();
