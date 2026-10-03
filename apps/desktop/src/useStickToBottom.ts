import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

const NEAR_BOTTOM = 80; // px from the end that still counts as "reading the newest text"
const INPUT_WINDOW_MS = 600; // a scroll this soon after wheel/touch/key/pointer input is the reader's own

/**
 * Keeps a scrolling list pinned to its newest content, like a chat window should.
 *
 * It follows while the reader is at (or near) the bottom, so streamed text, tool chips, approval cards and
 * banners that change the layout never leave the newest part below the fold. If the reader scrolls up to
 * read, it stops following until they scroll back down or `stick()` is called (a new message from them, or a
 * different conversation). Scrolling is instant: smooth scrolling restarts on every streamed chunk and ends
 * up short of a bottom that keeps moving.
 *
 * Put `ref` and `onScroll` on the scrolling element and `contentRef` on a wrapper around everything inside
 * it; the wrapper's size changes are what tell us the content grew.
 */
export function useStickToBottom<S extends HTMLElement, C extends HTMLElement>(deps: unknown[]) {
  const ref = useRef<S>(null);
  const contentRef = useRef<C>(null);
  const stuck = useRef(true);
  const observer = useRef<ResizeObserver | null>(null);
  const observed = useRef<[Element | null, Element | null]>([null, null]);
  const lastInput = useRef(0);

  const toBottom = useCallback(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // The browser also scrolls on its own when the layout changes (a narrower window re-wraps the text, which
  // fires a scroll event before we have caught up). Only the reader's own scrolling may let go of the bottom.
  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM;
    if (atBottom) stuck.current = true;
    else if (performance.now() - lastInput.current < INPUT_WINDOW_MS) stuck.current = false;
  }, []);

  useEffect(() => {
    const mark = () => {
      lastInput.current = performance.now();
    };
    const events = ["wheel", "touchstart", "touchmove", "pointerdown", "keydown"] as const;
    events.forEach((e) => window.addEventListener(e, mark, { passive: true, capture: true }));
    return () => events.forEach((e) => window.removeEventListener(e, mark, { capture: true }));
  }, []);

  /** Follow again from now on and jump to the end. */
  const stick = useCallback(() => {
    stuck.current = true;
    toBottom();
  }, [toBottom]);

  // Before the browser paints, so a new message never flashes in below the fold.
  useLayoutEffect(() => {
    if (stuck.current) toBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  // Content that grows or shrinks without a state change we know about: text wrapping, a banner above the
  // composer appearing, the window being resized. Runs after every render because the elements may not exist
  // yet on the first one (or may be replaced), and observing an element twice is harmless.
  useEffect(() => {
    if (!observer.current) {
      observer.current = new ResizeObserver(() => {
        if (stuck.current) toBottom();
      });
    }
    const now: [Element | null, Element | null] = [ref.current, contentRef.current];
    now.forEach((el, i) => {
      if (el === observed.current[i]) return;
      if (observed.current[i]) observer.current?.unobserve(observed.current[i]!);
      if (el) observer.current?.observe(el);
    });
    observed.current = now;
  });
  useEffect(() => () => observer.current?.disconnect(), []);

  return { ref, contentRef, onScroll, stick };
}
