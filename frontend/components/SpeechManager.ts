// Browser-native Web Speech API (STT & TTS per ARCHITECTURE.md Section 7)
// Enhanced with real-time interim results and MediaRecorder fallback to POST /api/transcribe

export interface SpeechState {
  isListening: boolean;
  audioRecording: boolean;
}

export class SpeechManager {
  private recognition: any = null;
  private mediaRecorder: MediaRecorder | null = null;
  private mediaStream: MediaStream | null = null;
  private audioChunks: Blob[] = [];
  private isCancelled: boolean = false;
  private baseText: string = '';

  public isListening: boolean = false;
  public audioRecording: boolean = false;

  constructor(
    private onResult?: (transcript: string) => void,
    private onStateChange?: (isActive: boolean, details?: SpeechState) => void,
    private apiUrl: string = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  ) {
    this.initRecognition();
  }

  private initRecognition() {
    if (typeof window !== 'undefined') {
      const SpeechRecognition =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SpeechRecognition) {
        try {
          this.recognition = new SpeechRecognition();
          this.recognition.continuous = false;
          this.recognition.interimResults = true;
          this.recognition.lang = 'en-US';

          this.recognition.onstart = () => {
            this.isListening = true;
            this.notifyState();
          };

          this.recognition.onresult = (event: any) => {
            let interimTranscript = '';
            let finalTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; ++i) {
              const res = event.results[i];
              if (res[0]) {
                if (res.isFinal) {
                  finalTranscript += res[0].transcript;
                } else {
                  interimTranscript += res[0].transcript;
                }
              }
            }

            const currentSpoken = (finalTranscript || interimTranscript).trim();
            if (currentSpoken) {
              const fullText = this.baseText ? `${this.baseText} ${currentSpoken}` : currentSpoken;
              this.onResult?.(fullText);
            }
          };

          this.recognition.onspeechend = () => {
            // Do not prematurely terminate recognition on pause
          };

          this.recognition.onend = () => {
            this.isListening = false;
            this.notifyState();
          };

          this.recognition.onerror = (event: any) => {
            if (event.error === 'no-speech') {
              // Ignore initial momentary silence
              return;
            }
            console.warn('Speech recognition error:', event.error);
            this.isListening = false;
            this.notifyState();
          };
        } catch (err) {
          console.warn('Could not instantiate SpeechRecognition:', err);
          this.recognition = null;
        }
      }
    }
  }

  public async startListening(baseText: string = '') {
    this.isCancelled = false;
    this.baseText = baseText.trim();

    // Primary STT: Real-time Browser SpeechRecognition
    if (this.recognition) {
      if (!this.isListening) {
        try {
          this.recognition.start();
        } catch (e) {
          console.warn('Failed to start SpeechRecognition:', e);
          this.isListening = false;
          this.notifyState();
        }
      }
      return;
    }

    // Fallback STT: MediaRecorder Blob sent to POST /api/transcribe
    if (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.mediaStream = stream;
        this.audioChunks = [];

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

        const options = mimeType ? { mimeType } : undefined;
        this.mediaRecorder = new MediaRecorder(stream, options);

        this.mediaRecorder.ondataavailable = (event: BlobEvent) => {
          if (event.data && event.data.size > 0) {
            this.audioChunks.push(event.data);
          }
        };

        this.mediaRecorder.onstop = async () => {
          this.audioRecording = false;
          this.notifyState();

          // Stop and release audio tracks
          if (this.mediaStream) {
            this.mediaStream.getTracks().forEach((track) => track.stop());
            this.mediaStream = null;
          }

          if (this.isCancelled || this.audioChunks.length === 0) {
            return;
          }

          // Send audio payload to POST /api/transcribe
          try {
            const blobType = mimeType || 'audio/webm';
            const audioBlob = new Blob(this.audioChunks, { type: blobType });
            const formData = new FormData();
            formData.append('file', audioBlob, 'audio.webm');

            const res = await fetch(`${this.apiUrl}/api/transcribe`, {
              method: 'POST',
              body: formData,
            });

            if (res.ok) {
              const data = await res.json();
              const transcript = (data.transcript || data.text || '').trim();
              if (transcript) {
                const fullText = this.baseText ? `${this.baseText} ${transcript}` : transcript;
                this.onResult?.(fullText);
              }
            } else {
              console.warn('Backend audio transcription returned status:', res.status);
            }
          } catch (err) {
            console.error('Error uploading audio for transcription:', err);
          }
        };

        this.mediaRecorder.start();
        this.audioRecording = true;
        this.notifyState();
      } catch (err) {
        console.warn('Microphone access denied or MediaRecorder failed:', err);
        this.audioRecording = false;
        this.notifyState();
      }
    } else {
      console.warn('Speech-to-Text unavailable: Neither SpeechRecognition nor getUserMedia supported.');
    }
  }

  public stopListening() {
    if (this.recognition && this.isListening) {
      try {
        this.recognition.stop();
      } catch (e) {
        console.warn('Failed to stop recognition:', e);
      }
    }
    if (this.mediaRecorder && this.audioRecording) {
      try {
        this.mediaRecorder.stop();
      } catch (e) {
        console.warn('Failed to stop MediaRecorder:', e);
      }
    }
  }

  public cancelListening() {
    this.isCancelled = true;
    if (this.recognition && this.isListening) {
      try {
        this.recognition.abort();
      } catch (e) {
        // ignore
      }
      this.isListening = false;
    }
    if (this.mediaRecorder && this.audioRecording) {
      try {
        this.mediaRecorder.stop();
      } catch (e) {
        // ignore
      }
      if (this.mediaStream) {
        this.mediaStream.getTracks().forEach((track) => track.stop());
        this.mediaStream = null;
      }
      this.audioRecording = false;
    }
    this.notifyState();
  }

  public toggleListening(baseText: string = '') {
    if (this.isListening || this.audioRecording) {
      this.stopListening();
    } else {
      this.startListening(baseText);
    }
  }

  private notifyState() {
    const active = this.isListening || this.audioRecording;
    this.onStateChange?.(active, {
      isListening: this.isListening,
      audioRecording: this.audioRecording,
    });
  }

  // TTS via window.speechSynthesis (free tier, zero latency)
  public speak(text: string) {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      // Cancel previous utterances
      window.speechSynthesis.cancel();

      // Keep speech concise per PRD.md Section 7 (< 20 words)
      const cleanText = text.replace(/[\*\#\_\[\]]/g, '').trim();
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      window.speechSynthesis.speak(utterance);
    }
  }
}
