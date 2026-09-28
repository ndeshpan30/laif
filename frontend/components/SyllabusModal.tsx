'use client';

import React, { useState } from 'react';
import { Upload, X, Check } from 'lucide-react';

interface SyllabusModalProps {
  isOpen: boolean;
  onClose: () => void;
  userId: string;
  apiUrl: string;
  onSuccess: (filename: string, modulesCount: number) => void;
}

export const SyllabusModal: React.FC<SyllabusModalProps> = ({
  isOpen,
  onClose,
  userId,
  apiUrl,
  onSuccess,
}) => {
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setIsUploading(true);
    setError(null);

    const formData = new FormData();
    formData.append('user_id', userId);
    if (subject.trim()) {
      formData.append('subject', subject.trim());
    }
    formData.append('file', file);

    try {
      const res = await fetch(`${apiUrl}/api/upload-syllabus`, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        throw new Error(`Upload failed: ${res.statusText}`);
      }

      const data = await res.json();
      onSuccess(data.filename, data.modules_ingested);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to upload syllabus');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-lg border border-[var(--text-ink)] bg-[var(--bg-paper)] p-6 shadow-[6px_6px_0px_0px_var(--text-ink)]">
        <div className="flex justify-between items-center mb-6 pb-2 border-b border-[var(--border-line)]">
          <h2 className="font-mono text-sm uppercase tracking-widest text-[var(--text-ink)]">
            Ingest Syllabus / Timetable PDF
          </h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-[var(--text-ink)] hover:text-[var(--bg-paper)] transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleUpload} className="space-y-4">
          <div>
            <label className="block font-mono text-xs uppercase tracking-wider mb-1 text-[var(--text-ink)]">
              Subject Name (Optional)
            </label>
            <input
              type="text"
              placeholder="e.g. Computer Networks"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full bujo-input py-2 text-sm text-[var(--text-ink)]"
            />
          </div>

          <div>
            <label className="block font-mono text-xs uppercase tracking-wider mb-2 text-[var(--text-ink)]">
              Course Syllabus / Lab Schedule PDF
            </label>
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              className="w-full font-mono text-xs text-[var(--text-ink)] file:mr-4 file:py-2 file:px-4 file:border-0 file:text-xs file:font-mono file:bg-[var(--text-ink)] file:text-[var(--bg-paper)] file:uppercase file:tracking-wider hover:file:opacity-90"
              required
            />
          </div>

          {error && (
            <p className="font-mono text-xs text-[var(--accent)] tracking-wide">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-3 pt-4 border-t border-[var(--border-line)]">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 border border-[var(--border-line)] font-mono text-xs uppercase tracking-widest text-[var(--text-ink)] hover:border-[var(--text-ink)]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!file || isUploading}
              className="px-5 py-2 bg-[var(--text-ink)] text-[var(--bg-paper)] font-mono text-xs uppercase tracking-widest disabled:opacity-50 hover:opacity-90 transition-opacity"
            >
              {isUploading ? 'Chunking & Embedding...' : 'Ingest to pgvector'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
