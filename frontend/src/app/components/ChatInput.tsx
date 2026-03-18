"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { detectUrl, detectPlatform, getPlatformLabel, getPlatformIcon, isUrl } from "../lib/api";

interface ChatInputProps {
  onSubmit: (text: string, url?: string) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSubmit, disabled }: ChatInputProps) {
  const [text, setText] = useState("");
  const [detectedUrl, setDetectedUrl] = useState<string | null>(null);
  const [detectedPlatform, setDetectedPlatform] = useState<string>("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [text]);

  // Detect URL in text
  useEffect(() => {
    const url = detectUrl(text);
    if (url) {
      setDetectedUrl(url);
      setDetectedPlatform(detectPlatform(url));
    } else {
      setDetectedUrl(null);
      setDetectedPlatform("");
    }
  }, [text]);

  function handleSubmit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;

    if (detectedUrl && isUrl(trimmed)) {
      // Text is just a URL -> pass as url parameter
      onSubmit("", detectedUrl);
    } else if (detectedUrl) {
      // Text contains a URL along with other text
      onSubmit(trimmed, detectedUrl);
    } else {
      onSubmit(trimmed);
    }

    setText("");
    setDetectedUrl(null);
    setDetectedPlatform("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    // Let the browser handle the paste normally, URL detection happens via useEffect
  }, []);

  function removeUrl() {
    if (!detectedUrl) return;
    setText((prev) => prev.replace(detectedUrl, "").trim());
    setDetectedUrl(null);
    setDetectedPlatform("");
  }

  const canSubmit = !disabled && text.trim().length > 0;

  return (
    <div className="w-full max-w-3xl mx-auto">
      {/* Link preview chip */}
      {detectedUrl && (
        <div className="flex items-center gap-2 mb-2 animate-fade-in">
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/10 border border-accent/20 text-xs">
            <span className="text-sm">{getPlatformIcon(detectedPlatform)}</span>
            <span className="font-medium text-accent">
              {getPlatformLabel(detectedPlatform)}
            </span>
            <span className="text-text-tertiary max-w-[200px] truncate">
              {detectedUrl.replace(/^https?:\/\/(?:www\.)?/, "").slice(0, 40)}
            </span>
            <button
              onClick={removeUrl}
              className="ml-1 text-text-tertiary hover:text-text-primary transition-colors"
              aria-label="Link entfernen"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <span className="text-[10px] text-text-tertiary">
            Inhalt wird automatisch extrahiert
          </span>
        </div>
      )}

      <div className="flex items-end gap-2 glass-inner rounded-xl px-3 py-2 transition-colors focus-within:border-text-tertiary/30">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          disabled={disabled}
          placeholder="Text, Artikel, Behauptung oder Link einfügen…"
          rows={1}
          className="flex-1 resize-none bg-transparent py-1 text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none disabled:opacity-40 leading-relaxed"
        />
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          aria-label="Analyse starten"
          className="shrink-0 mb-0.5 flex h-7 w-7 items-center justify-center rounded-full bg-accent text-white transition-colors hover:bg-accent-hover disabled:opacity-25 disabled:cursor-not-allowed"
        >
          {disabled ? (
            <span className="font-mono text-[10px] animate-blink">·</span>
          ) : (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="5" y1="12" x2="19" y2="12" />
              <polyline points="12 5 19 12 12 19" />
            </svg>
          )}
        </button>
      </div>
      <p className="mt-1.5 text-center text-[11px] text-text-tertiary">
        Shift+Enter für Zeilenumbruch · Links werden automatisch erkannt
      </p>
    </div>
  );
}
