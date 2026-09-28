'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useTheme } from 'next-themes';
import { Mic, MicOff, Send, Sun, Moon, FileUp, ArrowUpRight } from 'lucide-react';
import { soundManager } from '@/components/SoundManager';
import { SpeechManager } from '@/components/SpeechManager';
import { SyllabusModal } from '@/components/SyllabusModal';

interface Message {
  id: string;
  sender: 'user' | 'agent';
  text: string;
  timestamp: string;
  audioSignal?: string;
  scheduleStatus?: string;
  details?: any;
}

const DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001';
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export default function ConversationalPage() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      sender: 'agent',
      text: "Autonomous Cognitive Offloader active. State your goal, log a workout, or announce a deadline. Vague intentions will be interrogated.",
      timestamp: '08:00',
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [userId, setUserId] = useState(DEFAULT_USER_ID);
  const [isGrillMode, setIsGrillMode] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const speechManagerRef = useRef<SpeechManager | null>(null);

  useEffect(() => {
    setMounted(true);
    // Initialize Web Speech Manager
    speechManagerRef.current = new SpeechManager(
      (transcript) => {
        setInputValue(transcript);
      },
      (listening) => {
        setIsListening(listening);
      }
    );
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputValue).trim();
    if (!text || isLoading) return;

    const userMsg: Message = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputValue('');
    setIsLoading(true);

    try {
      const res = await fetch(`${API_URL}/api/conversation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          message: text,
          grill_mode: isGrillMode,
        }),
      });

      if (!res.ok) {
        throw new Error(`API error: ${res.statusText}`);
      }

      const data = await res.json();

      // Trigger audio feedback per ARCHITECTURE.md Section 4.7
      if (data.audio_signal === 'SUCCESS_JINGLE') {
        soundManager.playSuccessJingle();
      } else if (data.audio_signal === 'SAD_TROMBONE') {
        soundManager.playSadTrombone();
      }

      // Voice output via Web Speech API TTS
      speechManagerRef.current?.speak(data.reply);

      const agentMsg: Message = {
        id: `agent-${Date.now()}`,
        sender: 'agent',
        text: data.reply,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        audioSignal: data.audio_signal,
        scheduleStatus: data.schedule_status,
        details: data,
      };

      setMessages((prev) => [...prev, agentMsg]);
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          sender: 'agent',
          text: `Solver offline: ${err.message || 'Unable to connect to backend'}. Ensure FastAPI is running on port 8000.`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };


  return (
    <main className="min-h-screen flex flex-col justify-between max-w-4xl mx-auto px-4 py-6">
      {/* Minimal Top Navigation per UI_UX.md */}
      <header className="flex justify-between items-center pb-4 border-b border-[var(--border-line)] mb-6">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs uppercase tracking-widest font-bold text-[var(--text-ink)]">
            OFFLOADER
          </span>
          <span className="text-[var(--border-line)]">/</span>
          <span className="font-mono text-[11px] text-gray-500 uppercase tracking-widest">
            NEURO-SYMBOLIC CORE
          </span>
        </div>

        <div className="flex items-center gap-4">

          <button
            onClick={() => setIsModalOpen(true)}
            title="Upload Syllabus PDF"
            className="p-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] transition-colors"
          >
            <FileUp className="w-4 h-4" />
          </button>

          {mounted && (
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              title="Toggle Theme"
              className="p-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] transition-colors"
            >
              {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </button>
          )}

          <Link
            href="/web"
            className="flex items-center gap-1 px-3 py-2 border border-[var(--border-line)] text-[var(--text-ink)] hover:border-[var(--text-ink)] font-mono text-xs uppercase tracking-widest transition-colors"
          >
            <span>THE WEB</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </Link>

          <Link
            href="/ledger"
            className="flex items-center gap-1 px-3 py-2 bg-[var(--text-ink)] text-[var(--bg-paper)] font-mono text-xs uppercase tracking-widest hover:opacity-90 transition-opacity"
          >
            <span>THE LEDGER</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      </header>

      {/* Message Thread (Directly over dot grid, no wrapper card chrome) */}
      <div className="flex-1 overflow-y-auto space-y-6 pb-24">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex flex-col ${msg.sender === 'user' ? 'items-end' : 'items-start'}`}
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-gray-500">
                {msg.sender === 'user' ? 'YOU' : 'AGENT'}
              </span>
              <span className="font-mono text-[10px] text-gray-400">
                {msg.timestamp}
              </span>
              {msg.audioSignal === 'SAD_TROMBONE' && (
                <span className="font-mono text-[9px] uppercase tracking-wider text-[var(--accent)] bg-[var(--accent)]/10 px-1 border border-[var(--accent)]/30">
                  SAD TROMBONE
                </span>
              )}
            </div>

            <div
              className={`max-w-2xl p-4 text-sm leading-relaxed rounded-2xl ${
                msg.sender === 'user'
                  ? 'bg-[var(--text-ink)] text-[var(--bg-paper)] border border-[var(--text-ink)] shadow-[3px_3px_0px_0px_rgba(0,0,0,0.1)]'
                  : 'bg-[var(--bg-paper)] text-[var(--text-ink)] border border-[var(--border-line)] shadow-[3px_3px_0px_0px_rgba(0,0,0,0.05)]'
              }`}
            >
              {msg.text}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex flex-col items-start">
            <span className="font-mono text-[10px] uppercase tracking-widest text-gray-500 mb-1">
              AGENT
            </span>
            <div className="border border-[var(--border-line)] bg-[var(--bg-paper)] p-4 font-mono text-xs text-gray-400 animate-pulse rounded-2xl">
              Computing CP-SAT conflict resolution...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Pinned Bottom Input Bar (UI_UX.md Section 4: bottom-border only) */}
      <div className="fixed bottom-0 left-0 right-0 bg-[var(--bg-paper)]/95 backdrop-blur-sm border-t border-[var(--border-line)] py-4">
        <div className="max-w-4xl mx-auto px-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="flex items-center gap-3"
          >
            <button
              type="button"
              onClick={() => setIsGrillMode((prev) => !prev)}
              className={`rounded-none font-mono text-xs uppercase px-3 py-2 transition-colors select-none ${
                isGrillMode
                  ? 'bg-black text-white border border-black dark:bg-white dark:text-black dark:border-white'
                  : 'border border-black bg-transparent text-black dark:border-white dark:text-white'
              }`}
            >
              {isGrillMode ? '[ GRILL: ON ]' : '[ GRILL: OFF ]'}
            </button>

            <div className="flex-1 flex items-center border-b-2 border-[var(--text-ink)] pb-1">
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder={isGrillMode ? "Grill Mode: State your goal to be interrogated..." : "Log workout, state exam, or declare a habit..."}
                className="w-full bg-transparent font-mono text-sm text-[var(--text-ink)] placeholder:text-gray-400 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => speechManagerRef.current?.toggleListening()}
                title={isListening ? 'Stop listening' : 'Start voice input'}
                className={`p-2 transition-colors ${
                  isListening ? 'text-[var(--accent)] animate-pulse' : 'text-gray-400 hover:text-[var(--text-ink)]'
                }`}
              >
                {isListening ? <Mic className="w-5 h-5" /> : <MicOff className="w-5 h-5" />}
              </button>
            </div>

            <button
              type="submit"
              disabled={!inputValue.trim() || isLoading}
              className="px-5 py-2.5 bg-[var(--text-ink)] text-[var(--bg-paper)] font-mono text-xs uppercase tracking-widest disabled:opacity-40 hover:opacity-90 transition-opacity flex items-center gap-2"
            >
              <span>SEND</span>
              <Send className="w-3.5 h-3.5" />
            </button>
          </form>
        </div>
      </div>

      {/* Syllabus Modal */}
      <SyllabusModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        userId={userId}
        apiUrl={API_URL}
        onSuccess={(filename, count) => {
          setMessages((prev) => [
            ...prev,
            {
              id: `sys-${Date.now()}`,
              sender: 'agent',
              text: `Ingested syllabus '${filename}' into pgvector (${count} modules embedded). Ready for coursework query & exam planning.`,
              timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            },
          ]);
          soundManager.playSuccessJingle();
        }}
      />
    </main>
  );
}
