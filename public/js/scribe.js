// =========================================================================
// Ambient Scribe core browser flow for the consultation screen.
// Runs when the clinician opens /scribe and before any dev-only panel code.
// Owns theme, Mercure streams, microphone PCM streaming, recording controls,
// reconnect state, and the shared visit state used by transcript rendering.
// Transcript cards, replay, and summaries live in the focused scribe modules.
// =========================================================================

const THEME_STORAGE_KEY = 'ambient-scribe-theme';

const MEDICAL_ROLE_LABELS = { DOCTOR: 'Doctor', PATIENT: 'Patient' };
const MEDICAL_AVATAR_LABELS = { DOCTOR: 'Dr', PATIENT: 'Pt' };
const MEDICAL_ROLE_CYCLE = Object.keys(MEDICAL_ROLE_LABELS);
const SOCKET_STATE_LABELS = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'];
const MAX_RECONNECT_ATTEMPTS = 3;
const LOW_AUDIO_WARNING_FRAMES = 3;
const TARGET_AUDIO_SAMPLE_RATE = 16000;
const PCM_CHUNK_MS = 5000;

let transcriptionSocket = null;
let mediaStream = null;
let pcmStreamer = null;
let timerInterval = null;
let startTime = null;
let segmentIndex = 0;
let roleMapping = {};
let previousRoleMapping = {};
let confidence = 0;
let streams = null;
let isRecording = false;
let didUserStopRecording = false;
let reconnectAttempts = 0;
let reconnectTimer = null;
let lastSpeakerId = null;
let lastSegmentBlock = null;
let lowLevelCount = 0;
let latestQualityRecord = null;

const segmentsBySpeaker = new Map();
const manualOverrides = new Set();

/**
 * Converts backend role codes into labels the clinician sees on cards.
 * Use when a transcript card, dev log, or exported note needs a role label.
 */
function getRoleLabel(backendRole) {
    return MEDICAL_ROLE_LABELS[backendRole]
        ?? backendRole.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/**
 * Converts backend roles into compact initials shown in transcript avatars.
 * Use when rendering or relabeling a visible speaker card.
 */
function getAvatarLabel(backendRole) {
    return MEDICAL_AVATAR_LABELS[backendRole] ?? (backendRole === 'UNKNOWN' ? '?' : backendRole.charAt(0));
}

/**
 * Reads the page theme currently visible to the clinician.
 * Use when the theme button needs to decide the next light/dark state.
 */
function getCurrentTheme() {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

/**
 * Applies the chosen theme to the page and theme-toggle icon state.
 * Use when the page loads or the clinician toggles the display theme.
 */
function applyTheme(theme) {
    const resolvedTheme = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = resolvedTheme;

    const themeToggle = document.getElementById('themeToggle');
    const lightIcon = document.getElementById('themeIconLight');
    const darkIcon = document.getElementById('themeIconDark');

    // Theme controls may be absent in isolated tests, so keep the page usable.
    if (themeToggle) {
        themeToggle.setAttribute('aria-pressed', resolvedTheme === 'dark' ? 'true' : 'false');
    }

    // The visible icon mirrors what the clinician can switch away from.
    if (lightIcon) {
        lightIcon.style.display = resolvedTheme === 'dark' ? 'none' : '';
    }

    // The dark icon appears only when the page is currently dark.
    if (darkIcon) {
        darkIcon.style.display = resolvedTheme === 'dark' ? '' : 'none';
    }
}

/**
 * Flips the UI between light and dark themes.
 * Use when the clinician clicks the theme button in the header.
 */
function toggleTheme() {
    const nextTheme = getCurrentTheme() === 'dark' ? 'light' : 'dark';
    localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    applyTheme(nextTheme);
}

/**
 * Creates a DOM element without using HTML strings.
 * Use when transcript, summary, or status UI needs user-controlled text safely.
 */
function createElement(tagName, options = {}, children = []) {
    const element = document.createElement(tagName);

    // Class names describe how the clinician sees this element styled.
    if (options.className) {
        element.className = options.className;
    }

    // Text content keeps transcript and model output out of HTML parsing.
    if (options.text !== undefined) {
        element.textContent = options.text;
    }

    // Inline styles here mirror existing tiny status badges and dev-panel rows.
    if (options.style) {
        element.style.cssText = options.style;
    }

    // Attributes connect dynamic UI to accessibility and test selectors.
    if (options.attributes) {
        for (const [attributeName, attributeValue] of Object.entries(options.attributes)) {
            element.setAttribute(attributeName, attributeValue);
        }
    }

    // Data attributes keep speaker/session state attached to visible cards.
    if (options.dataset) {
        for (const [dataName, dataValue] of Object.entries(options.dataset)) {
            element.dataset[dataName] = dataValue;
        }
    }

    for (const child of children) {
        // Empty optional children mean the clinician simply does not see that badge.
        if (child) {
            element.appendChild(child);
        }
    }

    return element;
}

/**
 * Shows or hides an element by id.
 * Use when recording, replay, or summary steps reveal the next UI action.
 */
function setElementHidden(elementId, shouldHide) {
    const element = document.getElementById(elementId);

    // Missing optional controls should not block the main recording flow.
    if (!element) {
        return;
    }

    element.classList.toggle('hidden', shouldHide);
}

/**
 * Removes all children from a container without parsing HTML.
 * Use when resetting the visit, dev logs, or generated summary content.
 */
function clearElement(element) {
    // A missing optional panel means there is nothing for the clinician to clear.
    if (!element) {
        return;
    }

    element.replaceChildren();
}

/**
 * Creates the pulsing red dot used beside active recording-like statuses.
 * Use when the page needs to show live capture or replay activity.
 */
function createRecordingDot() {
    return createElement('span', { className: 'recording-dot recording-pulse' });
}

/**
 * Writes a status message beside a recording dot without HTML injection.
 * Use when the clinician starts recording, reconnects, or runs a demo replay.
 */
function setRecordingStatus(message, style = '') {
    const statusElement = document.getElementById('status');

    // The status region is optional in tests; recording still continues without it.
    if (!statusElement) {
        return;
    }

    const messageElement = createElement('span', { text: message, style });
    statusElement.replaceChildren(createRecordingDot(), messageElement);
}

/**
 * Writes plain status text to the live region.
 * Use when the clinician sees ready, error, reconnect, or completion messages.
 */
function setPlainStatus(message) {
    const statusElement = document.getElementById('status');

    // If the live region is absent, there is no visible status to update.
    if (!statusElement) {
        return;
    }

    statusElement.textContent = message;
}

applyTheme(localStorage.getItem(THEME_STORAGE_KEY) ?? getCurrentTheme());
