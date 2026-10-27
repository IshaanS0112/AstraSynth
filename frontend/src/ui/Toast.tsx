import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type ToastTone = "success" | "danger" | "info";

interface ToastItem {
  id: number;
  title: string;
  body?: string;
  tone: ToastTone;
}

type Notify = (toast: { title: string; body?: string; tone?: ToastTone }) => void;

const ToastContext = createContext<Notify>(() => undefined);

/** Confirmations for actions that finish out of sight: a job cancelled, a rover saved. */
export function useToast(): Notify {
  return useContext(ToastContext);
}

const ICONS = { success: CircleCheck, danger: CircleAlert, info: Info };
const TONE = { success: "text-success", danger: "text-danger", info: "text-info" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setItems((all) => all.filter((t) => t.id !== id)), []);

  const notify = useCallback<Notify>(
    ({ title, body, tone = "info" }) => {
      const id = nextId.current++;
      setItems((all) => [...all.slice(-3), { id, title, body, tone }]);
      window.setTimeout(() => dismiss(id), tone === "danger" ? 8000 : 5000);
    },
    [dismiss],
  );

  const value = useMemo(() => notify, [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 left-4 right-4 z-50 flex flex-col gap-2 sm:left-auto sm:w-96"
      >
        {items.map((toast) => {
          const Icon = ICONS[toast.tone];
          return (
            <div
              key={toast.id}
              role={toast.tone === "danger" ? "alert" : "status"}
              className="animate-rise pointer-events-auto flex items-start gap-3 rounded-lg border border-line-strong bg-surface-2 p-3"
            >
              <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${TONE[toast.tone]}`} strokeWidth={1.5} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">{toast.title}</p>
                {toast.body && <p className="mt-0.5 text-xs leading-5 text-muted">{toast.body}</p>}
              </div>
              <button
                aria-label="Dismiss"
                onClick={() => dismiss(toast.id)}
                className="-m-1 rounded p-1 text-subtle transition-colors duration-120 hover:text-fg"
              >
                <X className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
