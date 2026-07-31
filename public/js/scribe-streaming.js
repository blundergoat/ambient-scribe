// =========================================================================
// Ambient Scribe streaming and microphone helpers.
// Runs after shared page state is loaded and before recording controls.
// Owns Mercure reconnect behavior, Web Audio PCM conversion, demo WAV PCM
// streaming, and audio level UI.
// Users reach this code after starting a live consultation or replay feed.
// =========================================================================

/**
 * Keeps the Mercure event stream alive for the visible consultation.
 * Users reach this after recording starts or a demo replay subscribes to events.
 * All visit topics share ONE EventSource: browsers cap HTTP/1.1 connections per
 * host (~6), so per-topic streams across a few open tabs exhaust the pool and
 * new tabs hang silently in CONNECTING with a working hub. Events are routed
 * to handlers by their payload `type`.
 */
class StreamOrchestrator {
    /**
     * Stores the Mercure hub URL and prepares cleanup for page close.
     * Use once per visible consultation session.
     */
    constructor(mercureUrl) {
        this._mercureUrl = mercureUrl;
        this._topics = [];
        this._handlersByType = {};
        this._source = null;
        this._active = false;
        this._retries = 0;
        this._backoffMs = 1000;
        this._timer = null;
        this._lastEventId = null;
        window.addEventListener('beforeunload', () => this.disconnectAll());
    }

    /**
     * Opens one stream for every visit topic, routing events by payload type.
     * Use when the clinician starts a live or replay session.
     */
    connect(topics, handlersByType) {
        this._topics = topics;
        this._handlersByType = handlersByType;
        this._active = topics.length > 0;
        this._openStream();
    }

    /**
     * Closes the visit stream.
     * Use when the clinician stops, resets, or leaves the session.
     */
    disconnectAll() {
        this._active = false;
        clearTimeout(this._timer);
        this._source?.close();
        this._source = null;
    }

    /**
     * Reports whether the Mercure stream is open.
     * Use by the dev panel to show if the live transcript feed is connected.
     */
    get isConnected() {
        return this._source?.readyState === EventSource.OPEN;
    }

    /**
     * Counts Mercure reconnect attempts across the visible visit.
     * Use by the dev panel when diagnosing dropped transcript events.
     */
    get totalRetries() {
        return this._retries;
    }

    /**
     * Connects or reconnects the shared visit stream.
     * Bad JSON is logged and stream errors back off, so the visible transcript can recover.
     */
    _openStream() {
        // No active session or hub URL means no browser updates can be opened.
        if (!this._active || !this._mercureUrl) {
            return;
        }

        const streamUrl = new URL(this._mercureUrl);
        for (const topic of this._topics) {
            streamUrl.searchParams.append('topic', topic);
        }

        // Resume after a brief network drop so the clinician does not miss text.
        if (this._lastEventId) {
            streamUrl.searchParams.append('Last-Event-ID', this._lastEventId);
        }

        const source = new EventSource(streamUrl);
        this._source = source;

        source.onmessage = (event) => {
            this._backoffMs = 1000;
            this._retries = 0;

            // Store the last event id for a later reconnect of the UI feed.
            if (event.lastEventId) {
                this._lastEventId = event.lastEventId;
            }

            try {
                this._dispatch(JSON.parse(event.data));
            } catch (parseError) {
                console.error('Mercure event parse failed', parseError);
            }
        };

        source.onerror = () => {
            source.close();

            // Once the visit is stopped, reconnecting would resurrect stale UI.
            if (!this._active) {
                return;
            }

            this._retries++;
            this._timer = setTimeout(() => this._openStream(), this._backoffMs);
            this._backoffMs = Math.min(this._backoffMs * 2, 30000);
        };
    }

    /**
     * Routes one event to its handler by payload type.
     * Unknown types are ignored so new server events cannot break the UI.
     */
    _dispatch(payload) {
        const handler = this._handlersByType[payload?.type];
        handler?.(payload);
    }
}

/**
 * Converts microphone audio into 16 kHz PCM chunks for NeMo.
 * Users reach this after clicking Start Consultation and granting mic access.
 * Keep this contract aligned with NEMO_STREAM_INPUT_FORMAT=pcm.
 */
class PcmStreamer {
    /**
     * Stores the microphone stream and browser callbacks.
     * Use once per live recording socket.
     */
    constructor(stream, options = {}) {
        this._stream = stream;
        this._targetSampleRate = options.targetSampleRate ?? TARGET_AUDIO_SAMPLE_RATE;
        this._chunkMs = options.chunkMs ?? PCM_CHUNK_MS;
        this._onChunk = options.onChunk ?? (() => {});
        this._onAudioLevel = options.onAudioLevel ?? null;
        this._audioContext = null;
        this._source = null;
        this._processor = null;
        this._silence = null;
        this._buffers = [];
        this._bufferedBytes = 0;
        this._bytesPerChunk = this._targetSampleRate * 2 * (this._chunkMs / 1000);
    }

    /**
     * Starts browser audio processing for the active consultation.
     * Throws when Web Audio cannot support PCM, which the recording UI shows as setup failure.
     */
    async start() {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;

        // Without Web Audio support the clinician cannot stream microphone PCM.
        if (!AudioContextClass) {
            throw new Error('Web Audio API is not supported');
        }

        this._audioContext = new AudioContextClass();
        await this._audioContext.resume();

        this._source = this._audioContext.createMediaStreamSource(this._stream);
        this._processor = this._audioContext.createScriptProcessor(4096, 1, 1);
        this._silence = this._audioContext.createGain();
        this._silence.gain.value = 0;

        this._processor.onaudioprocess = (event) => {
            // Paused audio is not part of the visit: drop it before any
            // buffering so nothing leaks into the transcript later.
            if (this._paused) {
                return;
            }

            const microphoneSamples = event.inputBuffer.getChannelData(0);
            const downsampledSamples = this._downsampleBuffer(
                microphoneSamples,
                this._audioContext.sampleRate,
                this._targetSampleRate
            );
            const pcmChunk = floatTo16BitPcm(downsampledSamples);

            // Empty chunks mean there is no audio for the visible transcript yet.
            if (pcmChunk.byteLength === 0) {
                return;
            }

            // The audio meter shows whether the clinician is too quiet or clipping.
            if (this._onAudioLevel) {
                let sumSquares = 0;

                // RMS needs every downsampled frame to estimate current volume.
                for (let sampleIndex = 0; sampleIndex < downsampledSamples.length; sampleIndex++) {
                    sumSquares += downsampledSamples[sampleIndex] * downsampledSamples[sampleIndex];
                }

                this._onAudioLevel(Math.sqrt(sumSquares / downsampledSamples.length));
            }

            this._buffers.push(pcmChunk);
            this._bufferedBytes += pcmChunk.byteLength;

            // Send one stable chunk cadence to the server for smoother transcripts.
            if (this._bufferedBytes >= this._bytesPerChunk) {
                this.flush();
            }
        };

        this._source.connect(this._processor);
        this._processor.connect(this._silence);
        this._silence.connect(this._audioContext.destination);
    }

    /**
     * Sends buffered PCM to the active transcription socket.
     * Use on chunk boundaries and when the clinician stops recording.
     */
    flush() {
        // No buffered audio means the transcript has nothing new to receive.
        if (this._bufferedBytes === 0) {
            return;
        }

        const mergedChunk = new Uint8Array(this._bufferedBytes);
        let chunkOffset = 0;

        // Preserve microphone chunk order before sending to NeMo.
        for (const pcmChunk of this._buffers) {
            mergedChunk.set(pcmChunk, chunkOffset);
            chunkOffset += pcmChunk.byteLength;
        }

        this._buffers = [];
        this._bufferedBytes = 0;
        this._onChunk(mergedChunk.buffer);
    }

    /**
     * Suspends chunk emission without releasing the microphone or socket.
     * Use for a mid-visit pause: audio captured while paused is
     * dropped entirely - never buffered and never padded with silence - so
     * the server sees one seamless PCM stream when emission resumes.
     */
    pause() {
        // What was heard before the pause still belongs to the visit.
        this.flush();
        this._paused = true;
    }

    /**
     * Resumes chunk emission after a pause on the same session.
     */
    resume() {
        this._paused = false;
    }

    /**
     * Stops browser audio processing and releases Web Audio nodes.
     * Reports cleanup failures as warnings after the visible recording has stopped.
     */
    stop() {
        this.flush();
        this._processor?.disconnect();
        this._source?.disconnect();
        this._silence?.disconnect();
        this._processor = null;
        this._source = null;
        this._silence = null;

        // A close failure only affects cleanup after the visible stream has stopped.
        if (this._audioContext) {
            this._audioContext.close().catch((closeError) => {
                console.warn('Audio context cleanup failed after recording stopped', closeError);
            });
            this._audioContext = null;
        }
    }

    /**
     * Downsamples browser audio to the NeMo PCM sample rate.
     * Throws on impossible upsampling so the user sees an audio setup failure instead of bad text.
     */
    _downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
        // Matching sample rates can pass straight through to the transcript stream.
        if (inputSampleRate === outputSampleRate) {
            return new Float32Array(buffer);
        }

        // Upsampling would fake audio detail and degrade transcription quality.
        if (inputSampleRate < outputSampleRate) {
            throw new Error(`Cannot upsample audio from ${inputSampleRate}Hz to ${outputSampleRate}Hz`);
        }

        const sampleRatio = inputSampleRate / outputSampleRate;
        const outputLength = Math.round(buffer.length / sampleRatio);
        const outputBuffer = new Float32Array(outputLength);
        let outputIndex = 0;
        let inputIndex = 0;

        // Each output sample averages the browser frames that fall into its window.
        while (outputIndex < outputLength) {
            const nextInputIndex = Math.round((outputIndex + 1) * sampleRatio);
            let accumulatedSample = 0;
            let sampleCount = 0;

            // Average source samples so the clinician's speech timing remains stable.
            for (let sourceIndex = inputIndex; sourceIndex < nextInputIndex && sourceIndex < buffer.length; sourceIndex++) {
                accumulatedSample += buffer[sourceIndex];
                sampleCount++;
            }

            outputBuffer[outputIndex] = sampleCount > 0 ? accumulatedSample / sampleCount : 0;
            outputIndex++;
            inputIndex = nextInputIndex;
        }

        return outputBuffer;
    }

}

/**
 * Converts floating-point samples into signed 16-bit PCM bytes.
 * Shared by microphone capture and demo WAV replay so both feeds honour
 * the NEMO_STREAM_INPUT_FORMAT=pcm contract.
 */
function floatTo16BitPcm(floatBuffer) {
    const pcmBuffer = new Int16Array(floatBuffer.length);

    // Clamp each sample so clipping cannot overflow the PCM payload.
    for (let sampleIndex = 0; sampleIndex < floatBuffer.length; sampleIndex++) {
        const clampedSample = Math.max(-1, Math.min(1, floatBuffer[sampleIndex]));
        pcmBuffer[sampleIndex] = clampedSample < 0 ? clampedSample * 0x8000 : clampedSample * 0x7fff;
    }

    return new Uint8Array(pcmBuffer.buffer);
}

/**
 * Decodes a demo WAV file into the 16 kHz mono PCM bytes NeMo expects.
 * Use before replay streaming so the WAV enters the same PCM pipeline as
 * the microphone. Throws when the browser cannot decode the file, which
 * replay reports as a visible error.
 */
async function decodeWavToPcm(file) {
    const wavBytes = await file.arrayBuffer();
    // An offline context resamples during decode without opening an audible output.
    const decodingContext = new OfflineAudioContext(1, 1, TARGET_AUDIO_SAMPLE_RATE);
    const decodedBuffer = await decodingContext.decodeAudioData(wavBytes);

    return {
        pcmBytes: floatTo16BitPcm(mixToMonoSamples(decodedBuffer)),
        durationSeconds: decodedBuffer.duration,
    };
}

/**
 * Mixes a decoded audio buffer down to one channel of samples.
 * Use because manual WAV uploads can be stereo while NeMo expects mono.
 */
function mixToMonoSamples(decodedBuffer) {
    // Generated fixtures are already mono and can stream without mixing.
    if (decodedBuffer.numberOfChannels === 1) {
        return decodedBuffer.getChannelData(0);
    }

    const monoSamples = new Float32Array(decodedBuffer.length);

    // Averaging channels keeps both speakers audible in transcription.
    for (let channelIndex = 0; channelIndex < decodedBuffer.numberOfChannels; channelIndex++) {
        const channelSamples = decodedBuffer.getChannelData(channelIndex);

        for (let sampleIndex = 0; sampleIndex < channelSamples.length; sampleIndex++) {
            monoSamples[sampleIndex] += channelSamples[sampleIndex] / decodedBuffer.numberOfChannels;
        }
    }

    return monoSamples;
}

/**
 * Streams decoded demo WAV PCM over the live transcription socket.
 * Users reach this by playing demo audio; pacing follows the audible replay
 * clock so NeMo only receives audio the user has already heard, exactly as
 * it would from a live microphone.
 */
class WavPcmStreamer {
    /**
     * Stores the replay audio element, PCM bytes, and chunk callback.
     * Use once per demo replay session after the WAV is decoded.
     */
    constructor(audioElement, pcmBytes, options = {}) {
        this._audioElement = audioElement;
        this._pcmBytes = pcmBytes;
        this._sampleRate = options.sampleRate ?? TARGET_AUDIO_SAMPLE_RATE;
        this._chunkBytes = this._sampleRate * 2 * ((options.chunkMs ?? PCM_CHUNK_MS) / 1000);
        this._onChunk = options.onChunk ?? (() => {});
        this._sentBytes = 0;
        this._pumpInterval = null;
        this._boundPump = () => this.pump();
    }

    /**
     * Starts following the replay audio clock.
     * Use once the transcription socket is open and playback is starting.
     */
    start() {
        this._audioElement?.addEventListener('timeupdate', this._boundPump);
        // Background tabs throttle timeupdate events, so a coarse interval keeps chunks flowing.
        this._pumpInterval = setInterval(this._boundPump, 250);
    }

    /**
     * Sends every full chunk of audio the user has already heard.
     * Use on audio clock ticks; forward seeks send the skipped span as a burst.
     */
    pump() {
        const heardBytes = this._heardBytes();

        // Full chunks keep the cadence the live microphone path uses.
        while (heardBytes - this._sentBytes >= this._chunkBytes) {
            this._sendUpTo(this._sentBytes + this._chunkBytes);
        }
    }

    /**
     * Sends the partial chunk heard before an early stop.
     * Use so the transcript covers exactly the audio the user listened to.
     */
    flushHeard() {
        this._sendUpTo(this._heardBytes());
    }

    /**
     * Sends all remaining PCM after the WAV reaches its natural end.
     * Use so the transcript tail is not lost to chunk-boundary rounding.
     */
    finish() {
        this._sendUpTo(this._pcmBytes.byteLength);
    }

    /**
     * Stops following the audio clock.
     * Use on stop, completion, socket loss, or visit reset.
     */
    stop() {
        this._audioElement?.removeEventListener('timeupdate', this._boundPump);
        clearInterval(this._pumpInterval);
        this._pumpInterval = null;
    }

    /**
     * Converts the audible playback position into a PCM byte offset.
     * Whole samples keep the byte offset aligned to 16-bit frames.
     */
    _heardBytes() {
        const currentTimeSeconds = this._audioElement?.currentTime ?? 0;
        const heardSamples = Math.floor(currentTimeSeconds * this._sampleRate);

        return Math.min(heardSamples * 2, this._pcmBytes.byteLength);
    }

    /**
     * Sends unsent PCM up to the requested byte offset.
     * Backward seeks are ignored so NeMo never receives duplicate audio.
     */
    _sendUpTo(untilByte) {
        // Nothing new means a pause, a backward seek, or an already-flushed tail.
        if (untilByte <= this._sentBytes) {
            return;
        }

        const pcmChunk = this._pcmBytes.slice(this._sentBytes, untilByte);
        this._sentBytes = untilByte;
        this._onChunk(pcmChunk.buffer);
    }
}

/**
 * Updates the audio-level warning shown beside the recording status.
 * Use while the clinician is speaking into the microphone.
 */
function updateAudioLevel(rms) {
    const audioLevelElement = document.getElementById('audioLevel');

    // No meter on the page means there is no visible audio feedback to update.
    if (!audioLevelElement) {
        return;
    }

    // Clipping means the clinician is too loud and may lose words.
    if (rms > 0.95) {
        lowLevelCount = 0;
        setAudioLevelMessage(audioLevelElement, 'Audio clipping detected', '#ef4444', true);
        return;
    }

    // Repeated quiet frames mean the clinician may be too far from the mic.
    if (rms < 0.005) {
        lowLevelCount++;

        // Wait a few frames so short pauses do not flash a warning.
        if (lowLevelCount >= LOW_AUDIO_WARNING_FRAMES) {
            setAudioLevelMessage(audioLevelElement, 'Low audio level - move closer to the microphone', '#eab308', true);
        }

        return;
    }

    lowLevelCount = 0;
    setAudioLevelMessage(audioLevelElement, '', '#22c55e', false);
}

/**
 * Renders the tiny colored audio meter status safely.
 * Use when microphone energy changes while recording is active.
 */
function setAudioLevelMessage(audioLevelElement, message, color, includeMessage) {
    const dot = createElement('span', {
        style: `display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:4px;`,
    });

    audioLevelElement.classList.remove('hidden');
    audioLevelElement.style.color = color;
    audioLevelElement.replaceChildren(dot);

    // Warning text appears only when the clinician needs to adjust microphone use.
    if (includeMessage) {
        audioLevelElement.appendChild(document.createTextNode(message));
    }
}

/**
 * Hides microphone feedback after recording stops.
 * Use when the clinician stops, resets, or loses the live stream.
 */
function hideAudioLevel() {
    lowLevelCount = 0;
    const audioLevelElement = document.getElementById('audioLevel');

    // The meter is optional in tests and hidden layouts.
    if (audioLevelElement) {
        audioLevelElement.classList.add('hidden');
        clearElement(audioLevelElement);
    }
}

/**
 * Closes any Mercure streams tied to the visible visit.
 * Use before starting, stopping, replaying, or resetting the consultation.
 */
function disconnectMercureStreams() {
    streams?.disconnectAll();
    streams = null;
}

/**
 * Shows that role inference is waiting for enough transcript context.
 * Use immediately after the clinician starts a live consultation.
 */
function showRoleIdentificationPending() {
    // Role updates can be disabled for local or degraded environments.
    if (!CONFIG.enableRoleUpdates) {
        return;
    }

    const badge = document.getElementById('confidenceBadge');

    // Without the badge, the transcript can still render normally.
    if (!badge) {
        return;
    }

    badge.classList.remove('hidden');
    badge.textContent = 'Identifying speakers…';
    badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600';
}
