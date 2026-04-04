"use client";

import { useState, useEffect, useCallback } from "react";
import { WidgetId, DEFAULT_WIDGET_ORDER } from "./types";

const LAYOUT_KEY = "fng_dashboard_layout";
const VISIBLE_KEY = "fng_dashboard_visible";

function defaultVisibility(): Record<WidgetId, boolean> {
  return Object.fromEntries(DEFAULT_WIDGET_ORDER.map((id) => [id, true])) as Record<WidgetId, boolean>;
}

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function useDashboardLayout() {
  const [order, setOrder] = useState<WidgetId[]>(DEFAULT_WIDGET_ORDER);
  const [visible, setVisible] = useState<Record<WidgetId, boolean>>(defaultVisibility);

  useEffect(() => {
    const savedOrder = readJson<WidgetId[]>(LAYOUT_KEY, DEFAULT_WIDGET_ORDER);
    // Ensure all widget IDs are present (forward-compat)
    const validOrder = savedOrder.filter((id) => DEFAULT_WIDGET_ORDER.includes(id));
    for (const id of DEFAULT_WIDGET_ORDER) {
      if (!validOrder.includes(id)) validOrder.push(id);
    }
    setOrder(validOrder);
    setVisible(readJson(VISIBLE_KEY, defaultVisibility()));
  }, []);

  const reorder = useCallback((newOrder: WidgetId[]) => {
    setOrder(newOrder);
    if (typeof window !== "undefined") {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(newOrder));
    }
  }, []);

  const toggleVisibility = useCallback((id: WidgetId) => {
    setVisible((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      // Ensure at least one widget stays visible
      if (!Object.values(next).some(Boolean)) return prev;
      if (typeof window !== "undefined") {
        localStorage.setItem(VISIBLE_KEY, JSON.stringify(next));
      }
      return next;
    });
  }, []);

  return { order, visible, reorder, toggleVisibility };
}
