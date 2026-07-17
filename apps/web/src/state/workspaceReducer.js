export const initialWorkspaceState = {
  surface: "home",
  rail: "atlas",
  railOpen: false,
  sidebarOpen: true,
  toolsPage: false
};

export function workspaceReducer(state, action) {
  switch (action.type) {
    case "setSurface":
      return { ...state, surface: action.value };
    case "setRail":
      return { ...state, rail: action.value };
    case "setRailOpen":
      return { ...state, railOpen: action.value };
    case "setSidebarOpen":
      return { ...state, sidebarOpen: action.value };
    case "setToolsPage":
      return { ...state, toolsPage: action.value };
    case "openTools":
      return { ...state, toolsPage: true, surface: "atlas", railOpen: false };
    case "openContext":
      return { ...state, rail: "context", railOpen: true };
    case "focusDetail":
      return { ...state, rail: "detail", railOpen: true };
    case "clearDetail":
      return { ...state, rail: "atlas" };
    default:
      return state;
  }
}
