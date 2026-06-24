import { create } from 'zustand'

export interface UIContext {
  route: string
  tab?: string
  drama?: string
}

interface UIContextState {
  ctx: UIContext
  setUIContext: (ctx: UIContext) => void
  setDrama: (drama: string | null) => void
}

export const useUIContextStore = create<UIContextState>((set) => ({
  ctx: { route: '/' },
  setUIContext: (ctx) => set({ ctx }),
  setDrama: (drama) =>
    set((state) => ({
      ctx: drama !== null ? { ...state.ctx, drama } : { route: state.ctx.route, tab: state.ctx.tab },
    })),
}))
