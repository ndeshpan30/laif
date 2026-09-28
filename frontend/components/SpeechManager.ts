// Browser-native Web Speech API (STT & TTS per ARCHITECTURE.md Section 7)

export class SpeechManager {
  private recognition: any = null;
  private isListening: boolean = false;

  constructor(
    private onResult?: (transcript: string) => void,
    private onStateChange?: (listening: boolean) => void
  ) {
    if (typeof window !== 'undefined') {
      const SpeechRecognition =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SpeechRecognition) {
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = false;
        this.recognition.lang = 'en-US';

        this.recognition.onstart = () => {
          this.isListening = true;
          this.onStateChange?.(true);
        };

        this.recognition.onend = () => {
          this.isListening = false;
          this.onStateChange?.(false);
        };

        this.recognition.onresult = (event: any) => {
          if (event.results && event.results[0]) {
            const transcript = event.results[0][0].transcript;
            this.onResult?.(transcript);
          }
        };

        this.recognition.onerror = (event: any) => {
          console.warn('Speech recognition error:', event.error);
          this.isListening = false;
          this.onStateChange?.(false);
        };
      }
    }
  }

  startListening() {
    if (this.recognition && !this.isListening) {
      try {
        this.recognition.start();
      } catch (e) {
        console.warn('Failed to start recognition:', e);
      }
    }
  }

  stopListening() {
    if (this.recognition && this.isListening) {
      try {
        this.recognition.stop();
      } catch (e) {
        console.warn('Failed to stop recognition:', e);
      }
    }
  }

  toggleListening() {
    if (this.isListening) {
      this.stopListening();
    } else {
      this.startListening();
    }
  }

  // TTS via window.speechSynthesis (free tier, zero latency)
  speak(text: string) {
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
