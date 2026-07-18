import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const TOOLTIP_ID = "studio-global-tooltip";
const HOVER_DELAY_MS = 260;

type ActiveTooltip = {
  target: HTMLElement;
  text: string;
  rect: DOMRect;
};

type TooltipPosition = {
  left: number;
  top: number;
};

function tooltipTarget(eventTarget: EventTarget | null) {
  return eventTarget instanceof Element
    ? eventTarget.closest<HTMLElement>("[title], [data-studio-tooltip]")
    : null;
}

export default function GlobalTooltip() {
  const [active, setActive] = useState<ActiveTooltip | null>(null);
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState<TooltipPosition>({ left: 0, top: 0 });
  const tooltipRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<ActiveTooltip | null>(null);
  const timerRef = useRef<number | null>(null);
  const descriptionsRef = useRef(new WeakMap<HTMLElement, string | null>());

  useEffect(() => {
    const cancelTimer = () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
    };

    const restoreTarget = (target: HTMLElement) => {
      const text = target.dataset.studioTooltip;
      if (text && !target.hasAttribute("title")) target.setAttribute("title", text);
      delete target.dataset.studioTooltip;
      const previousDescription = descriptionsRef.current.get(target);
      if (previousDescription === null) target.removeAttribute("aria-describedby");
      else if (previousDescription !== undefined) target.setAttribute("aria-describedby", previousDescription);
      descriptionsRef.current.delete(target);
    };

    const deactivate = () => {
      cancelTimer();
      const current = activeRef.current;
      if (current) restoreTarget(current.target);
      activeRef.current = null;
      setVisible(false);
      setActive(null);
    };

    const activate = (target: HTMLElement, immediate: boolean) => {
      const current = activeRef.current;
      if (current?.target === target) return;
      if (current) restoreTarget(current.target);
      cancelTimer();

      const text = target.getAttribute("title") ?? target.dataset.studioTooltip;
      if (!text?.trim()) return;
      target.dataset.studioTooltip = text;
      target.removeAttribute("title");
      descriptionsRef.current.set(target, target.getAttribute("aria-describedby"));
      target.setAttribute("aria-describedby", TOOLTIP_ID);
      const next = { target, text, rect: target.getBoundingClientRect() };
      activeRef.current = next;
      setActive(next);
      setVisible(false);
      const show = () => {
        if (activeRef.current?.target === target) setVisible(true);
      };
      if (immediate) show();
      else timerRef.current = window.setTimeout(show, HOVER_DELAY_MS);
    };

    const onPointerOver = (event: PointerEvent) => {
      const target = tooltipTarget(event.target);
      if (target) activate(target, false);
    };
    const onPointerOut = (event: PointerEvent) => {
      const current = activeRef.current;
      const relatedTarget = event.relatedTarget instanceof Node ? event.relatedTarget : null;
      if (!current || current.target.contains(relatedTarget)) return;
      if (current.target.contains(document.activeElement)) return;
      deactivate();
    };
    const onFocusIn = (event: FocusEvent) => {
      const target = tooltipTarget(event.target);
      if (target) activate(target, true);
    };
    const onFocusOut = (event: FocusEvent) => {
      const current = activeRef.current;
      const relatedTarget = event.relatedTarget instanceof Node ? event.relatedTarget : null;
      if (!current || current.target.contains(relatedTarget)) return;
      if (current.target.matches(":hover")) return;
      deactivate();
    };
    const onViewportChange = () => {
      const current = activeRef.current;
      if (!current) return;
      const next = { ...current, rect: current.target.getBoundingClientRect() };
      activeRef.current = next;
      setActive(next);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") deactivate();
    };

    document.addEventListener("pointerover", onPointerOver);
    document.addEventListener("pointerout", onPointerOut);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      document.removeEventListener("pointerover", onPointerOver);
      document.removeEventListener("pointerout", onPointerOut);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
      cancelTimer();
      const current = activeRef.current;
      if (current) restoreTarget(current.target);
      activeRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    if (!active || !visible || !tooltipRef.current) return;
    const tooltip = tooltipRef.current.getBoundingClientRect();
    const margin = 8;
    const gap = 7;
    const centered = active.rect.left + active.rect.width / 2 - tooltip.width / 2;
    const left = Math.min(Math.max(margin, centered), window.innerWidth - tooltip.width - margin);
    const below = active.rect.bottom + gap;
    const top = below + tooltip.height <= window.innerHeight - margin
      ? below
      : Math.max(margin, active.rect.top - tooltip.height - gap);
    setPosition({ left, top });
  }, [active, visible]);

  if (!active) return null;
  return createPortal(
    <div
      id={TOOLTIP_ID}
      ref={tooltipRef}
      className={visible ? "global-tooltip visible" : "global-tooltip"}
      role="tooltip"
      style={{ left: position.left, top: position.top }}
    >
      {active.text.split("\n").map((line, index) => <span key={`${index}-${line}`}>{line}</span>)}
    </div>,
    document.body,
  );
}
