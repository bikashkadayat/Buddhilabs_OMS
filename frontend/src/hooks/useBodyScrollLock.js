import { useEffect } from 'react';

/**
 * Lock <body> scroll while a modal/overlay is open; restore the previous value
 * on close or unmount. Pass a boolean that is true while the modal is open.
 */
export function useBodyScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [active]);
}

export default useBodyScrollLock;
