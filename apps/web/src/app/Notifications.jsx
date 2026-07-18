import { createContext, useCallback, useContext, useMemo, useReducer } from "react";

const NotificationContext = createContext(null);

function reducer(state, action) {
  if (action.type === "dismiss") return state.filter((item) => item.id !== action.id);
  if (action.type === "announce") return [...state.slice(-3), action.item];
  return state;
}

export function NotificationProvider({ children }) {
  const [items, dispatch] = useReducer(reducer, []);
  const announce = useCallback((message, tone = "info") => {
    if (!message) return;
    const item = { id: `${Date.now()}-${Math.random()}`, message, tone };
    dispatch({ type: "announce", item });
    window.setTimeout(() => dispatch({ type: "dismiss", id: item.id }), tone === "error" ? 8000 : 4200);
  }, []);
  const value = useMemo(() => ({ announce }), [announce]);
  return (
    <NotificationContext.Provider value={value}>
      {children}
      <div className="notification-region" aria-live="polite" aria-atomic="false">
        {items.map((item) => <div className={`notification-item tone-${item.tone}`} key={item.id}>{item.message}</div>)}
      </div>
    </NotificationContext.Provider>
  );
}

export function useNotifications() {
  return useContext(NotificationContext) || { announce: () => {} };
}
