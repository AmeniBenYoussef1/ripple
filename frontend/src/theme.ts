// Light / dark theme: the organizer's choice (saved in this browser), else the system preference.
import { createElement, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

export type Theme = "light" | "dark";
export type ThemeChoice = Theme | "system";
const KEY = "ripple.theme";

const systemTheme = (): Theme =>
  window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";

export function getChoice(): ThemeChoice {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function resolve(choice: ThemeChoice): Theme {
  return choice === "system" ? systemTheme() : choice;
}

export function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

/** Call once before React renders, so the page never flashes the wrong theme. */
export function initTheme() {
  apply(resolve(getChoice()));
}

const listeners = new Set<() => void>();

export function setChoice(choice: ThemeChoice) {
  if (choice === "system") localStorage.removeItem(KEY);
  else localStorage.setItem(KEY, choice);
  apply(resolve(choice));
  listeners.forEach((l) => l());
}

/** [current theme, saved choice, setter] — every component using it stays in sync. */
export function useTheme(): [Theme, ThemeChoice, (c: ThemeChoice) => void] {
  const [, tick] = useState(0);
  useEffect(() => {
    const update = () => tick((n) => n + 1);
    listeners.add(update);
    // follow the system while the organizer hasn't chosen
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onSystem = () => { if (getChoice() === "system") { apply(systemTheme()); update(); } };
    mq?.addEventListener("change", onSystem);
    return () => { listeners.delete(update); mq?.removeEventListener("change", onSystem); };
  }, []);
  const choice = getChoice();
  return [resolve(choice), choice, setChoice];
}

/** A small sun/moon button: switches between light and dark (and saves the choice). */
export function ThemeToggle() {
  const [theme, , set] = useTheme();
  const next: Theme = theme === "dark" ? "light" : "dark";
  return createElement("button", {
    type: "button", className: "theme-toggle", onClick: () => set(next),
    "aria-label": `Switch to ${next} mode`, title: `Switch to ${next} mode`,
  }, createElement(theme === "dark" ? Sun : Moon, { size: 18 }));
}
