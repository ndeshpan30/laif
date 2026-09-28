'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, MicOff, Send, Loader2 } from 'lucide-react';

export interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (text?: string) => void;
  isLoading?: boolean;
  isGrillMode?: boolean;
  onToggleGrillMode?: () => void;
  isOnboarding?: boolean;
  placeholder?: string;
  apiUrl?: string;
  speechManager?: any;
}

export function ChatInput({
  value,
  onChange,
  onSend,
  isLoading = false,
  isGrillMode = false,
  onToggleGrillMode,
  isOnboarding = false,
  placeholder,
  apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
}: ChatInputProps) {
  // Visual states
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);

  // Stable refs to prevent React closure stale states & re-render cancellation loops
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recognitionRef = useRef<any>(null);
  const isRecordingRef = useRef<boolean>(false);
  const isCancelledRef = useRef<boolean>(false);
  const baseTextRef = useRef<string>('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Stop recording and finalize audio dispatch
  const stopRecording = useCallback(() => {
    if (!isRecordingRef.current) return;
    isRecordingRef.current = false;
    setIsRecording(false);

    // Stop real-time SpeechRecognition if active
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch (err) {
        // ignore
      }
      recognitionRef.current = null;
    }

    // Stop MediaRecorder (triggers recorder.onstop to send blob to /api/transcribe)
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch (err) {
        console.warn('MediaRecorder stop error:', err);
      }
    }
  }, []);

  // Cancel recording immediately without transcribing
  const cancelRecording = useCallback(() => {
    if (!isRecordingRef.current) return;
    isCancelledRef.current = true;
    isRecordingRef.current = false;
    setIsRecording(false);

    // Abort speech recognition
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch (err) {
        // ignore
      }
      recognitionRef.current = null;
    }

    // Stop media recorder without triggering transcript processing
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch (err) {
        // ignore
      }
    }

    // Stop and release audio hardware tracks
    if (mediaStreamRef.current) {
      try {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      } catch (err) {
        // ignore
      }
      mediaStreamRef.current = null;
    }

    audioChunksRef.current = [];
  }, []);

  // Start recording via MediaRecorder with optional SpeechRecognition preview
  const startRecording = useCallback(async () => {
    if (isRecordingRef.current) return;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      alert('Microphone access is not supported in this browser.');
      return;
    }

    isRecordingRef.current = true;
    isCancelledRef.current = false;
    baseTextRef.current = value.trim();
    setIsRecording(true);

    try {
      // 1. Request microphone stream
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // Guard: Ensure tracks are live
      const audioTracks = stream.getAudioTracks();
      if (!audioTracks || audioTracks.length === 0 || audioTracks[0].readyState === 'ended') {
        throw new Error('Microphone stream track is not available.');
      }

      mediaStreamRef.current = stream;
      audioChunksRef.current = [];

      // 2. Select optimal MIME type
      let mimeType = 'audio/webm';
      if (typeof MediaRecorder !== 'undefined') {
        if (!MediaRecorder.isTypeSupported('audio/webm')) {
          if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
            mimeType = 'audio/webm;codecs=opus';
          } else if (MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) {
            mimeType = 'audio/ogg;codecs=opus';
          } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
            mimeType = 'audio/mp4';
          } else {
            mimeType = '';
          }
        }
      }

      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event: BlobEvent) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        // Immediately release microphone hardware
        if (mediaStreamRef.current) {
          try {
            mediaStreamRef.current.getTracks().forEach((track) => track.stop());
          } catch (err) {
            // ignore
          }
          mediaStreamRef.current = null;
        }

        // Skip upload if cancelled or empty
        if (isCancelledRef.current || audioChunksRef.current.length === 0) {
          audioChunksRef.current = [];
          return;
        }

        const blobType = mimeType || 'audio/webm';
        const audioBlob = new Blob(audioChunksRef.current, { type: blobType });
        audioChunksRef.current = [];

        if (audioBlob.size < 100) return;

        // 3. Dispatch to local WhimprFlow backend pipeline
        try {
          setIsTranscribing(true);
          const formData = new FormData();
          formData.append('file', audioBlob, 'recording.webm');

          const res = await fetch(`${apiUrl}/api/transcribe`, {
            method: 'POST',
            body: formData,
          });

          if (res.ok) {
            const data = await res.json();
            const transcript = (data.transcript || data.text || '').trim();
            if (transcript) {
              const fullText = baseTextRef.current
                ? `${baseTextRef.current} ${transcript}`
                : transcript;
              onChange(fullText);
            }
          } else {
            console.warn('POST /api/transcribe failed with status:', res.status);
          }
        } catch (err) {
          console.error('Audio transcription request failed:', err);
        } finally {
          setIsTranscribing(false);
        }
      };

      // Start recording with timeslice chunks
      recorder.start(250);

      // 4. Optional parallel live preview via SpeechRecognition (if available & functional)
      if (typeof window !== 'undefined') {
        const SpeechRecognition =
          (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

        if (SpeechRecognition) {
          try {
            const recognition = new SpeechRecognition();
            recognition.continuous = false;
            recognition.interimResults = true;
            recognition.lang = 'en-US';

            recognition.onresult = (event: any) => {
              let interim = '';
              let final = '';
              for (let i = event.resultIndex; i < event.results.length; ++i) {
                const part = event.results[i][0]?.transcript || '';
                if (event.results[i].isFinal) {
                  final += part;
                } else {
                  interim += part;
                }
              }
              const currentText = (final || interim).trim();
              if (currentText) {
                const fullText = baseTextRef.current
                  ? `${baseTextRef.current} ${currentText}`
                  : currentText;
                onChange(fullText);
              }
            };

            // Root Cause 1: Never terminate on 'no-speech' or temporary pause
            recognition.onerror = (event: any) => {
              if (event.error === 'no-speech') {
                // Ignore momentary silence; MediaRecorder continues capturing
                return;
              }
              console.warn('Browser SpeechRecognition warning:', event.error);
            };

            // Root Cause 1: Do NOT stop recorder inside onspeechend
            recognition.onend = () => {
              recognitionRef.current = null;
            };

            recognitionRef.current = recognition;
            recognition.start();
          } catch (err) {
            // SpeechRecognition failure is non-fatal: MediaRecorder handles full ASR
            console.warn('SpeechRecognition preview disabled:', err);
          }
        }
      }
    } catch (err: any) {
      console.error('Failed to start audio recording:', err);
      isRecordingRef.current = false;
      setIsRecording(false);

      if (mediaStreamRef.current) {
        try {
          mediaStreamRef.current.getTracks().forEach((track) => track.stop());
        } catch (e) {
          // ignore
        }
        mediaStreamRef.current = null;
      }

      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        alert('Microphone access was denied. Please allow microphone permissions in your browser settings.');
      } else {
        alert('Unable to access microphone. Please ensure an audio input device is connected.');
      }
    }
  }, [apiUrl, onChange, value]);

  // Atomic toggle handler with event bubbling prevention
  const handleMicClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    e.stopPropagation();

    if (isRecordingRef.current) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  // Global Escape key listener to cancel recording
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isRecordingRef.current) {
        e.preventDefault();
        cancelRecording();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [cancelRecording]);

  // Clean up media tracks on unmount only
  useEffect(() => {
    return () => {
      if (mediaStreamRef.current) {
        try {
          mediaStreamRef.current.getTracks().forEach((track) => track.stop());
        } catch (e) {
          // ignore
        }
      }
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        try {
          mediaRecorderRef.current.stop();
        } catch (e) {
          // ignore
        }
      }
    };
  }, []);

  // Textarea keyboard handler (Enter to send, Shift+Enter for newline, Esc to cancel)
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() && !isLoading && !isTranscribing) {
        onSend();
      }
    } else if (e.key === 'Escape') {
      if (isRecordingRef.current) {
        e.preventDefault();
        cancelRecording();
      }
    }
  };

  const dynamicPlaceholder =
    placeholder ||
    (isOnboarding
      ? "Tell me about your semester, routine, classes, or goals (or say 'skip')..."
      : isGrillMode
      ? "Grill Mode: State your goal to be interrogated..."
      : "Log workout, state exam, or declare a habit...");

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim() && !isLoading && !isTranscribing) {
          onSend();
        }
      }}
      className="flex items-end gap-3"
    >
      {/* Optional Grill Mode Toggle */}
      {onToggleGrillMode && (
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            onToggleGrillMode();
          }}
          className={`rounded-none font-mono text-xs uppercase px-3 py-2 transition-colors select-none mb-1 ${
            isGrillMode
              ? 'bg-black text-white border border-black dark:bg-white dark:text-black dark:border-white'
              : 'border border-black bg-transparent text-black dark:border-white dark:text-white'
          }`}
        >
          {isGrillMode ? '[ GRILL: ON ]' : '[ GRILL: OFF ]'}
        </button>
      )}

      {/* Input Text Area and Microphone Button */}
      <div className="flex-1 flex items-center border-b-2 border-[var(--text-ink)] pb-1 min-h-[42px]">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={isRecording ? 'Listening... Speak now (click mic to stop, Esc to cancel)' : dynamicPlaceholder}
          className="w-full bg-transparent font-mono text-sm text-[var(--text-ink)] placeholder:text-gray-400 focus:outline-none resize-none py-1.5 leading-relaxed max-h-32"
          style={{ height: 'auto' }}
        />

        {/* Microphone Button with Active Red Pulsing State & Transcribing State */}
        <button
          type="button"
          onClick={handleMicClick}
          disabled={isLoading || isTranscribing}
          title={
            isRecording
              ? 'Recording audio... (Click to finish, Esc to cancel)'
              : isTranscribing
              ? 'Transcribing audio with local Whisper...'
              : 'Start voice input (Speech-to-Text)'
          }
          className={`p-2 transition-colors flex-shrink-0 flex items-center justify-center select-none ${
            isRecording
              ? 'animate-pulse text-red-500'
              : isTranscribing
              ? 'text-amber-500'
              : 'text-gray-400 hover:text-[var(--text-ink)]'
          }`}
          aria-label={isRecording ? 'Stop recording' : 'Start microphone'}
        >
          {isRecording ? (
            <Mic className="w-5 h-5 text-red-500 animate-pulse" />
          ) : isTranscribing ? (
            <Loader2 className="w-5 h-5 animate-spin text-amber-500" />
          ) : (
            <MicOff className="w-5 h-5" />
          )}
        </button>
      </div>

      {/* Submit / Send Button */}
      <button
        type="submit"
        disabled={!value.trim() || isLoading || isTranscribing}
        className="px-5 py-2.5 bg-[var(--text-ink)] text-[var(--bg-paper)] font-mono text-xs uppercase tracking-widest disabled:opacity-40 hover:opacity-90 transition-opacity flex items-center gap-2 mb-1 flex-shrink-0"
      >
        <span>SEND</span>
        <Send className="w-3.5 h-3.5" />
      </button>
    </form>
  );
}

export default ChatInput;
