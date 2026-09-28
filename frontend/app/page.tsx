'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useTheme } from 'next-themes';
import { Mic, MicOff, Send, Sun, Moon, FileUp, ArrowUpRight, Check } from 'lucide-react';
import { soundManager } from '@/components/SoundManager';
import { SpeechManager } from '@/components/SpeechManager';
import { SyllabusModal } from '@/components/SyllabusModal';
import { ChatInput } from '@/components/ChatInput';

interface Message {
  id: string;
  sender: 'user' | 'agent';
  text: string;
  timestamp: string;
  audioSignal?: string;
  scheduleStatus?: string;
  extractedFacts?: string[];
  extractedTopics?: string[];
  details?: any;
}

const DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001';
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const OFFICIAL_OPENING_MESSAGE =
  "Hey, I'm here to take the 'what should I be doing right now?' load off your head. " +
  "To do that I need to know your life. Not a form, just talk. Tell me about a normal week: " +
  "what you're studying, what your days look like, what you do for fun, who and what takes your time, " +
  "what's stressing you out. Messy is fine. Write as much or as little as you want, and I'll ask about anything I'm missing. " +
  "You can say 'skip' to anything.";

export default function ConversationalPage() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [userId] = useState(DEFAULT_USER_ID);

  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      sender: 'agent',
      text: OFFICIAL_OPENING_MESSAGE,
      timestamp: '08:00',
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isGrillMode, setIsGrillMode] = useState(false);
  const [isOnboarding, setIsOnboarding] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const speechManagerRef = useRef<SpeechManager | null>(null);

  // Check onboarding status on load
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

    const checkStatus = async () => {
      try {
        const res = await fetch(`${API_URL}/api/conversation/status?user_id=${userId}`);
        if (res.ok) {
          const data = await res.json();
          setIsOnboarding(data.is_onboarding);
          if (data.is_onboarding && data.opening_message) {
            setMessages([
              {
                id: 'welcome',
                sender: 'agent',
                text: data.opening_message,
                timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              },
            ]);
          } else if (!data.is_onboarding) {
            setMessages([
              {
                id: 'welcome',
                sender: 'agent',
                text: "Autonomous Cognitive Offloader active. State your goal, log a workout, or announce a deadline. Vague intentions will be interrogated.",
                timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              },
            ]);
          }
        }
      } catch (err) {
        console.warn('Could not fetch onboarding status:', err);
      }
    };
    checkStatus();
  }, [userId]);

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
          is_onboarding: isOnboarding,
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

      // If onboarding just graduated or exited
      if (data.onboarding && data.onboarding.is_complete) {
        setIsOnboarding(false);
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
        extractedFacts: data.onboarding?.extracted_facts || [],
        extractedTopics: data.onboarding?.extracted_topics || [],
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
            {isOnboarding ? 'INTAKE // DESCRIBE YOUR LIFE' : 'NEURO-SYMBOLIC CORE'}
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

      {/* Message Thread */}
      <div className="flex-1 overflow-y-auto space-y-6 pb-28">
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
              className={`max-w-2xl p-4 text-sm leading-relaxed rounded-2xl whitespace-pre-wrap ${
                msg.sender === 'user'
                  ? 'bg-[var(--text-ink)] text-[var(--bg-paper)] border border-[var(--text-ink)] shadow-[3px_3px_0px_0px_rgba(0,0,0,0.1)]'
                  : 'bg-[var(--bg-paper)] text-[var(--text-ink)] border border-[var(--border-line)] shadow-[3px_3px_0px_0px_rgba(0,0,0,0.05)]'
              }`}
            >
              {msg.text}
            </div>

            {/* Editable "Here's what I got" Verification Card */}
            {msg.extractedFacts && msg.extractedFacts.length > 0 && (
              <div className="mt-3 w-full max-w-2xl p-4 border border-[var(--border-line)] bg-black/[0.02] dark:bg-white/[0.02] shadow-[2px_2px_0px_0px_rgba(0,0,0,0.05)]">
                <div className="flex items-center justify-between pb-2 mb-2.5 border-b border-[var(--border-line)]">
                  <span className="font-mono text-[10px] uppercase tracking-widest font-bold text-[var(--accent)]">
                    [ HERE&apos;S WHAT I GOT ]
                  </span>
                  <span className="font-mono text-[9px] uppercase tracking-wider text-gray-400">
                    VERIFIED FACTS RECORDED
                  </span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {msg.extractedFacts.map((fact, idx) => (
                    <span
                      key={idx}
                      className="inline-flex items-center gap-1.5 font-mono text-xs px-2.5 py-1 bg-[var(--bg-paper)] border border-[var(--border-line)] text-[var(--text-ink)] shadow-sm"
                    >
                      <Check className="w-3 h-3 text-[var(--accent)]" />
                      <span>{fact}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
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

      {/* Pinned Bottom Input Bar with Speech-to-Text Integration */}
      <div className="fixed bottom-0 left-0 right-0 bg-[var(--bg-paper)]/95 backdrop-blur-sm border-t border-[var(--border-line)] py-4">
        <div className="max-w-4xl mx-auto px-4">
          <ChatInput
            value={inputValue}
            onChange={setInputValue}
            onSend={() => handleSendMessage()}
            isLoading={isLoading}
            isGrillMode={isGrillMode}
            onToggleGrillMode={!isOnboarding ? () => setIsGrillMode((prev) => !prev) : undefined}
            isOnboarding={isOnboarding}
            apiUrl={API_URL}
          />
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
