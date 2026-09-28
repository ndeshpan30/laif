// ARCHITECTURE.md Section 4.7: Gamified Audio Feedback via pure Web Audio API

class SoundManager {
  private ctx: AudioContext | null = null;

  private initContext() {
    if (!this.ctx && typeof window !== 'undefined') {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (AudioCtx) {
        this.ctx = new AudioCtx();
      }
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
  }

  // Dopamine Jingle: ascending C major arpeggio C5 -> E5 -> G5 -> C6
  playSuccessJingle() {
    try {
      this.initContext();
      if (!this.ctx) return;
      const notes = [523.25, 659.25, 783.99, 1046.50];
      notes.forEach((freq, idx) => {
        if (!this.ctx) return;
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, this.ctx.currentTime + idx * 0.08);
        gain.gain.setValueAtTime(0.2, this.ctx.currentTime + idx * 0.08);
        gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + idx * 0.08 + 0.25);
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        osc.start(this.ctx.currentTime + idx * 0.08);
        osc.stop(this.ctx.currentTime + idx * 0.08 + 0.3);
      });
    } catch (e) {
      console.warn('Audio playback error:', e);
    }
  }

  // Sad Trombone: descending brass slide Eb4 -> D4 -> Db4 -> C4 with pitch bend
  playSadTrombone() {
    try {
      this.initContext();
      if (!this.ctx) return;
      const notes = [311.13, 293.66, 277.18, 261.63];
      notes.forEach((freq, idx) => {
        if (!this.ctx) return;
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        const startTime = this.ctx.currentTime + idx * 0.32;
        const duration = idx === 3 ? 0.8 : 0.3;
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(freq, startTime);
        if (idx === 3) {
          osc.frequency.exponentialRampToValueAtTime(215.0, startTime + duration);
        }
        gain.gain.setValueAtTime(0.18, startTime);
        gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration);
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        osc.start(startTime);
        osc.stop(startTime + duration);
      });
    } catch (e) {
      console.warn('Audio playback error:', e);
    }
  }
}

export const soundManager = new SoundManager();
