"use client";

import { useState, useEffect, useRef, useCallback, type ReactNode } from "react";
import { WidgetId, WIDGET_META } from "./types";

interface Props {
  order: WidgetId[];
  onReorder: (ids: WidgetId[]) => void;
  children: Record<WidgetId, ReactNode>;
}

export function DashboardGrid({ order, onReorder, children }: Props) {
  const [isDesktop, setIsDesktop] = useState(false);
  const [dragId, setDragId] = useState<WidgetId | null>(null);
  const [dropTarget, setDropTarget] = useState<WidgetId | null>(null);
  const dragCounterRef = useRef(0);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    setIsDesktop(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const handleDragStart = useCallback(
    (e: React.DragEvent, id: WidgetId) => {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", id);
      setDragId(id);
    },
    [],
  );

  const handleDragOver = useCallback((e: React.DragEvent, id: WidgetId) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDropTarget(id);
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current++;
  }, []);

  const handleDragLeave = useCallback(() => {
    dragCounterRef.current--;
    if (dragCounterRef.current <= 0) {
      setDropTarget(null);
      dragCounterRef.current = 0;
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent, targetId: WidgetId) => {
      e.preventDefault();
      const sourceId = e.dataTransfer.getData("text/plain") as WidgetId;
      if (sourceId === targetId) {
        setDragId(null);
        setDropTarget(null);
        dragCounterRef.current = 0;
        return;
      }
      const newOrder = order.filter((id) => id !== sourceId);
      const targetIdx = newOrder.indexOf(targetId);
      newOrder.splice(targetIdx, 0, sourceId);
      onReorder(newOrder);
      setDragId(null);
      setDropTarget(null);
      dragCounterRef.current = 0;
    },
    [order, onReorder],
  );

  const handleDragEnd = useCallback(() => {
    setDragId(null);
    setDropTarget(null);
    dragCounterRef.current = 0;
  }, []);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {order.map((id) => {
        const meta = WIDGET_META[id];
        const child = children[id];
        if (!child) return null;
        const isDragging = dragId === id;
        const isOver = dropTarget === id && dragId !== id;

        return (
          <div
            key={id}
            className={`${meta.colSpan === 2 ? "lg:col-span-2" : ""} relative group transition-opacity ${
              isDragging ? "opacity-40" : ""
            } ${isOver ? "ring-2 ring-accent rounded-xl" : ""}`}
            draggable={isDesktop}
            onDragStart={(e) => handleDragStart(e, id)}
            onDragOver={(e) => handleDragOver(e, id)}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDrop={(e) => handleDrop(e, id)}
            onDragEnd={handleDragEnd}
          >
            {isDesktop && (
              <div className="absolute top-2 left-2 z-10 opacity-0 group-hover:opacity-60 transition-opacity cursor-grab active:cursor-grabbing text-text-tertiary">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="8" cy="4" r="2" />
                  <circle cx="16" cy="4" r="2" />
                  <circle cx="8" cy="12" r="2" />
                  <circle cx="16" cy="12" r="2" />
                  <circle cx="8" cy="20" r="2" />
                  <circle cx="16" cy="20" r="2" />
                </svg>
              </div>
            )}
            {child}
          </div>
        );
      })}
    </div>
  );
}
