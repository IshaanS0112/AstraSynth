import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A dialog: Escape and the backdrop close it, focus stays inside while it is
 * open and returns to whatever opened it when it closes.
 */
export default function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "max-w-lg",
  placement = "center",
  bare = false,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
  placement?: "center" | "top";
  bare?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  // Held in a ref so a parent passing a fresh closure each render does not
  // re-run the effect, which would steal focus back to the first field.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Start in the field that wants it; otherwise on the dialog itself, so a
    // close button does not open with a focus ring around it.
    const first = panel.current?.querySelector<HTMLElement>("[data-autofocus]") ?? panel.current;
    first?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const head = items[0];
      const tail = items[items.length - 1];
      if (event.shiftKey && document.activeElement === head) {
        event.preventDefault();
        tail.focus();
      } else if (!event.shiftKey && document.activeElement === tail) {
        event.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className={`fixed inset-0 z-40 flex justify-center bg-canvas/70 px-4 ${
        placement === "top" ? "items-start pt-16 sm:pt-24" : "items-center py-6"
      }`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`animate-rise flex max-h-full w-full flex-col overflow-hidden rounded-lg border border-line-strong bg-surface-2 focus-visible:ring-0 focus-visible:ring-offset-0 ${width}`}
      >
        {bare ? (
          children
        ) : (
          <>
            <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
              <div>
                <h2 className="text-base font-semibold text-fg">{title}</h2>
                {description && <p className="mt-0.5 text-xs leading-5 text-muted">{description}</p>}
              </div>
              <button
                aria-label="Close"
                onClick={onClose}
                className="-m-1 rounded p-1 text-subtle transition-colors duration-120 hover:text-fg"
              >
                <X className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
            {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
