import { create } from 'zustand'
import type { Material, Creative } from '../types'

interface DetailDrawerState {
  /** 素材详情 Drawer */
  selectedMaterial: Material | null
  openMaterialDetail: (m: Material) => void
  closeMaterialDetail: () => void

  /** 成片详情 Drawer */
  selectedCreative: Creative | null
  openCreativeDetail: (c: Creative) => void
  closeCreativeDetail: () => void
}

export const useDetailDrawerStore = create<DetailDrawerState>((set) => ({
  selectedMaterial: null,
  selectedCreative: null,

  openMaterialDetail: (m) => set({ selectedMaterial: m }),
  closeMaterialDetail: () => set({ selectedMaterial: null }),

  openCreativeDetail: (c) => set({ selectedCreative: c }),
  closeCreativeDetail: () => set({ selectedCreative: null }),
}))
