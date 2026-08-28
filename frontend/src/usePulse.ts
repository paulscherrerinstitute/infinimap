import { useEffect, useRef } from "react";

// Flash an element each time `nonce` changes, as feedback for an action.
//
// The Web Animations API rather than a CSS class: repeating the action must
// restart the flash from frame 0, and re-applying an already-present class does
// not (React bails out of the re-render entirely).
//
// `keyframes` is a dep, so pass a module-level constant, not an inline array.
export function usePulse<T extends HTMLElement>(
  nonce: number | undefined,
  keyframes: Keyframe[],
  duration = 450,
) {
  const ref = useRef<T>(null);

  useEffect(() => {
    if (nonce === undefined) return; // inert until this element is the target
    const el = ref.current;
    if (!el) return;
    const anim = el.animate(keyframes, { duration, easing: "ease-out" });
    return () => anim.cancel();
  }, [nonce, keyframes, duration]);

  return ref;
}
