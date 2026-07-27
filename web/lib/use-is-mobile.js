"use client";
import { useEffect, useState } from "react";

// True below Tailwind's `md` (768px). Lazy initializer reads matchMedia on the
// client only (returns false during SSR), so there's no hydration mismatch.
export function useIsMobile(breakpoint = 768) {
  const [isMobile, setIsMobile] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia(`(max-width:${breakpoint - 1}px)`).matches
  );
  useEffect(() => {
    const mq = window.matchMedia(`(max-width:${breakpoint - 1}px)`);
    const on = () => setIsMobile(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [breakpoint]);
  return isMobile;
}
